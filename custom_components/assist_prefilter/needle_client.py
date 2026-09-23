"""Optional Needle 3 sidecar. Never blocks the voice path on failure."""

from __future__ import annotations

import logging
from typing import Any

from .const import DEFAULT_NEEDLE_TIMEOUT_MS
from .filter import FilterResult

_LOGGER = logging.getLogger(__name__)


def _candidate_payload(result: FilterResult) -> dict[str, Any]:
    return {
        "areas": [
            {"area_id": area.area_id, "name": area.name, "aliases": list(area.aliases)}
            for area in result.areas
        ],
        "entities": [
            {
                "entity_id": ent.entity_id,
                "name": ent.name,
                "aliases": list(ent.aliases),
                "area": ent.area_name,
                "domain": ent.domain,
                "state": ent.state,
            }
            for ent in result.entities
        ],
    }


def _apply_selection(
    result: FilterResult,
    entity_ids: list[str],
    area_ids: list[str],
) -> FilterResult:
    """Subset heuristic hits using Needle's select_targets output."""
    wanted_entities = {eid for eid in entity_ids if eid}
    wanted_areas = {aid for aid in area_ids if aid}
    entities = (
        [e for e in result.entities if e.entity_id in wanted_entities]
        if wanted_entities
        else list(result.entities)
    )
    if wanted_areas:
        areas = [a for a in result.areas if a.area_id in wanted_areas]
        if not entities:
            entities = [e for e in result.entities if e.area_id in wanted_areas]
    else:
        areas = list(result.areas)
    entity_scores = {
        e.entity_id: result.entity_scores.get(e.entity_id, 0) for e in entities
    }
    return FilterResult(
        folded_text=result.folded_text,
        tokens=result.tokens,
        expanded_tokens=result.expanded_tokens,
        areas=areas,
        entities=entities,
        area_scores=result.area_scores,
        entity_scores=entity_scores,
        satellite_area_id=result.satellite_area_id,
        is_query=result.is_query,
        action=result.action,
    )


def accepted_light_command(
    action: str | None,
    allowed_entity_ids: set[str],
    payload: Any,
) -> list[str] | None:
    """Return entity ids to act on, or None when the answer must not be executed.

    The service has to match the spoken action, and every returned id has to be
    one of the lights the prefilter already selected.
    """
    if action not in {"turn_on", "turn_off"} or not isinstance(payload, dict):
        return None
    if not allowed_entity_ids:
        return None
    service = payload.get("service")
    found = [str(item) for item in payload.get("entity_ids") or [] if item]
    if service != action or not found:
        return None
    if any(entity_id not in allowed_entity_ids for entity_id in found):
        return None
    unique: list[str] = []
    for entity_id in found:
        if entity_id not in unique:
            unique.append(entity_id)
    return unique


async def needle_execute(
    hass: Any,
    url: str,
    text: str,
    result: FilterResult,
    *,
    timeout_ms: int = DEFAULT_NEEDLE_TIMEOUT_MS,
) -> list[str] | None:
    """Ask Needle to turn the selected lights on or off.

    None means do not execute. The caller then uses the language model.
    """
    if result.action not in {"turn_on", "turn_off"} or not url:
        return None
    allowed = {
        entity.entity_id
        for entity in result.entities
        if entity.domain == "light"
    }
    if not allowed:
        return None

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    payload = {
        "utterance": text,
        "action": result.action,
        "candidates": _candidate_payload(result),
    }
    timeout_s = max(timeout_ms, 50) / 1000.0
    session = async_get_clientsession(hass)
    try:
        from aiohttp import ClientTimeout

        async with session.post(
            url, json=payload, timeout=ClientTimeout(total=timeout_s)
        ) as resp:
            if resp.status >= 400:
                _LOGGER.debug("Needle HTTP %s — leaving the command to the LLM", resp.status)
                return None
            data = await resp.json(content_type=None)
    except Exception as err:
        _LOGGER.debug("Needle unavailable (%s) — leaving the command to the LLM", err)
        return None
    accepted = accepted_light_command(result.action, allowed, data)
    if not accepted:
        _LOGGER.debug("Needle answer was not executable")
    return accepted


async def needle_refine(
    hass: Any,
    url: str,
    text: str,
    result: FilterResult,
    *,
    timeout_ms: int = DEFAULT_NEEDLE_TIMEOUT_MS,
) -> FilterResult:
    """Ask Needle to subset already-filtered candidates.

    On timeout or any error, return the heuristic result unchanged.
    """
    if not url or not result.entities:
        return result

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    payload = {
        "utterance": text,
        "candidates": _candidate_payload(result),
        "tools": [
            {
                "name": "select_targets",
                "parameters": {
                    "areas": "list[str]",
                    "entity_ids": "list[str]",
                    "intent": "control|query|other",
                },
            }
        ],
    }
    timeout_s = max(timeout_ms, 50) / 1000.0
    session = async_get_clientsession(hass)
    try:
        from aiohttp import ClientTimeout

        async with session.post(
            url, json=payload, timeout=ClientTimeout(total=timeout_s)
        ) as resp:
            if resp.status >= 400:
                _LOGGER.debug("Needle HTTP %s — keeping heuristic hits", resp.status)
                return result
            data = await resp.json(content_type=None)
    except Exception as err:  # timeout, DNS, malformed JSON
        _LOGGER.debug("Needle unavailable (%s) — keeping heuristic hits", err)
        return result

    entity_ids, area_ids = _parse_select_targets(data)
    if not entity_ids and not area_ids:
        return result
    refined = _apply_selection(result, entity_ids, area_ids)
    if not refined.entities and not refined.areas:
        return result
    return refined


def _parse_select_targets(data: Any) -> tuple[list[str], list[str]]:
    """Accept a few sidecar JSON shapes."""
    if not isinstance(data, dict):
        return [], []
    # Direct: {"entity_ids": [...], "areas": [...]}
    if "entity_ids" in data or "areas" in data:
        return (
            [str(x) for x in data.get("entity_ids") or []],
            [str(x) for x in data.get("areas") or []],
        )
    # Nested tool call: {"tool": "select_targets", "arguments": {...}}
    args = data.get("arguments") or data.get("tool_input") or {}
    if data.get("tool") == "select_targets" or data.get("name") == "select_targets":
        if isinstance(args, dict):
            return (
                [str(x) for x in args.get("entity_ids") or []],
                [str(x) for x in args.get("areas") or []],
            )
    calls = data.get("tool_calls") or data.get("calls") or []
    if isinstance(calls, list):
        for call in calls:
            if not isinstance(call, dict):
                continue
            name = call.get("name") or call.get("tool")
            if name != "select_targets":
                continue
            cargs = call.get("arguments") or call.get("args") or {}
            if isinstance(cargs, dict):
                return (
                    [str(x) for x in cargs.get("entity_ids") or []],
                    [str(x) for x in cargs.get("areas") or []],
                )
    return [], []
