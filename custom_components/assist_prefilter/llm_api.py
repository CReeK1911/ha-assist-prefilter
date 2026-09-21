"""LLM API that keeps Assist tools but replaces the full-house Static Context.

Home Assistant's Assist API dumps every exposed entity into the system prompt.
Selecting this API on the downstream agent (instead of Assist) makes that dump
the prefilter's hit list for the current request.
"""

from __future__ import annotations

import logging
from typing import Any

from .const import DOMAIN
from .filter_store import peek_pending

_LOGGER = logging.getLogger(__name__)

API_NAME = "Assist Prefilter"
LIVE_CONTEXT_SUFFIX = "GetLiveContext"
_NO_MATCH = "No matching devices for this request. Do not guess entity ids."


def replace_static_yaml(prompt: str, full_yaml: str, filtered_yaml: str) -> str:
    """Swap Assist's full entity YAML for the filtered dump. No-op if not found."""
    if not full_yaml or full_yaml not in prompt:
        return prompt
    return prompt.replace(full_yaml, filtered_yaml, 1)


def restrict_exposed(
    exposed: dict[str, Any], allowed: frozenset[str]
) -> dict[str, Any]:
    """Keep only entity ids from the current prefilter result."""
    return {entity_id: info for entity_id, info in exposed.items() if entity_id in allowed}


def _as_tool(inner: Any, allowed: frozenset[str]) -> Any:
    """Return a real llm.Tool so conversation agents accept the wrapper."""
    from homeassistant.helpers.llm import Tool

    logic = _FilteredLiveContext(inner, allowed)

    class _Tool(Tool):
        name = logic.name
        description = logic.description
        parameters = logic.parameters

        async def async_call(self, hass: Any, tool_input: Any, llm_context: Any) -> Any:
            return await logic.async_call(hass, tool_input, llm_context)

    return _Tool()


def _is_live_context(tool: Any) -> bool:
    name = getattr(tool, "name", "") or ""
    return name == "GetLiveContext" or name.endswith(LIVE_CONTEXT_SUFFIX)


class _FilteredLiveContext:
    """GetLiveContext that only returns the prefilter's entities.

    Not a homeassistant.helpers.llm.Tool subclass at import time so unit tests
    can load this module without Home Assistant. The instance is given Tool's
    behaviour by copying name, description, and parameters from the real tool
    and being registered as that tool's replacement.
    """

    def __init__(self, inner: Any, allowed: frozenset[str]) -> None:
        self.name = inner.name
        self.description = inner.description
        self.parameters = inner.parameters
        self._inner = inner
        self._allowed = allowed

    async def async_call(self, hass: Any, tool_input: Any, llm_context: Any) -> Any:
        """Return live state for the filtered set only."""
        if not self._allowed:
            return {"success": True, "result": _NO_MATCH}
        try:
            from homeassistant.components.homeassistant.llm import (
                async_get_exposed_entities,
            )
            from homeassistant.util import yaml as yaml_util
        except ImportError:
            return await self._inner.async_call(hass, tool_input, llm_context)

        exposed = restrict_exposed(
            async_get_exposed_entities(hass, llm_context.assistant, include_state=True),
            self._allowed,
        )
        exposed = _apply_live_filters(exposed, tool_input)
        if not exposed:
            return {"success": True, "result": _NO_MATCH}
        body = yaml_util.dump(list(exposed.values()))
        return {
            "success": True,
            "result": (
                "Live Context: An overview of the areas"
                " and the devices in this smart home:\n"
                f"{body}"
            ),
        }


def _apply_live_filters(exposed: dict[str, Any], tool_input: Any) -> dict[str, Any]:
    """Cheap name/domain/area filter so a tool call cannot widen the set."""
    args = getattr(tool_input, "tool_args", None) or {}
    name = str(args.get("name") or "").casefold()
    area = str(args.get("area") or "").casefold()
    domain = args.get("domain")
    domains: set[str] = set()
    if isinstance(domain, str) and domain.strip():
        domains.add(domain.strip().casefold())
    elif isinstance(domain, list):
        domains = {str(item).strip().casefold() for item in domain if str(item).strip()}

    if not name and not area and not domains:
        return exposed

    kept: dict[str, Any] = {}
    for entity_id, info in exposed.items():
        if domains and str(info.get("domain", "")).casefold() not in domains:
            continue
        blob = " ".join(
            str(info.get(key, "")) for key in ("names", "areas")
        ).casefold()
        if name and name not in blob and name not in entity_id.casefold():
            continue
        if area and area not in blob:
            continue
        kept[entity_id] = info
    return kept


def _yaml_dump(items: list[Any]) -> str:
    from homeassistant.util import yaml as yaml_util

    return yaml_util.dump(items)


def _load_exposed(hass: Any, assistant: str) -> dict[str, Any] | None:
    try:
        from homeassistant.components.homeassistant.llm import async_get_exposed_entities
    except ImportError:
        return None
    return async_get_exposed_entities(hass, assistant, include_state=False)


class FilteredAssistAPI:
    """Registered as an LLM API named Assist Prefilter.

    Subclassing happens at runtime so this module imports without Home Assistant.
    """

    def __new__(cls, hass: Any) -> Any:
        from homeassistant.helpers import llm as ha_llm

        class _API(ha_llm.API):
            def __init__(self) -> None:
                super().__init__(hass=hass, id=DOMAIN, name=API_NAME)

            async def async_get_api_instance(self, llm_context: Any) -> Any:
                assist = await ha_llm.async_get_api(
                    hass, ha_llm.LLM_API_ASSIST, llm_context
                )
                allowed = peek_pending(hass, getattr(llm_context, "context", None))
                prompt = assist.api_prompt
                tools = list(assist.tools)
                if allowed is not None:
                    prompt = _shrink_prompt(hass, llm_context, prompt, allowed)
                    tools = [
                        _as_tool(tool, allowed) if _is_live_context(tool) else tool
                        for tool in tools
                    ]
                return ha_llm.APIInstance(
                    api=self,
                    api_prompt=prompt,
                    llm_context=llm_context,
                    tools=tools,
                    custom_serializer=assist.custom_serializer,
                )

        return _API()


def _shrink_prompt(hass: Any, llm_context: Any, prompt: str, allowed: frozenset[str]) -> str:
    assistant = getattr(llm_context, "assistant", None) or "conversation"
    exposed = _load_exposed(hass, assistant)
    if not exposed:
        _LOGGER.debug("Could not load Assist exposed entities; leaving prompt unchanged")
        return prompt
    full_yaml = _yaml_dump(list(exposed.values()))
    filtered_items = [info for entity_id, info in exposed.items() if entity_id in allowed]
    filtered_yaml = _yaml_dump(filtered_items) if filtered_items else "[]\n"
    shrunk = replace_static_yaml(prompt, full_yaml, filtered_yaml)
    if shrunk == prompt:
        _LOGGER.debug("Static Context YAML was not found in the Assist prompt")
    else:
        _LOGGER.debug(
            "Static Context reduced from %s to %s entities",
            len(exposed),
            len(filtered_items),
        )
    return shrunk


def async_register_filtered_api(hass: Any) -> None:
    """Register the API once. Safe to call from async_setup."""
    root = hass.data.setdefault(DOMAIN, {})
    if root.get("llm_api_registered"):
        return
    try:
        from homeassistant.helpers import llm as ha_llm
    except ImportError:
        _LOGGER.debug("homeassistant.helpers.llm is unavailable")
        return
    try:
        unsub = ha_llm.async_register_api(hass, FilteredAssistAPI(hass))
    except Exception as err:  # already registered, or HA too old
        _LOGGER.debug("Could not register Assist Prefilter LLM API: %s", err)
        return
    root["llm_api_registered"] = True
    root["llm_api_unsub"] = unsub
