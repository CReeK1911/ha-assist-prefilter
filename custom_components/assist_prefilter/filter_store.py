"""Hold the current utterance's entity hits until the downstream LLM builds its prompt."""

from __future__ import annotations

from typing import Any

from .const import DOMAIN

PENDING_FILTERS = "pending_filters"


def _bucket(hass: Any) -> dict[str, frozenset[str]]:
    root = hass.data.setdefault(DOMAIN, {})
    return root.setdefault(PENDING_FILTERS, {})


def context_id(context: Any) -> str | None:
    """Context.id is shared with the downstream LLM API call."""
    if context is None:
        return None
    ident = getattr(context, "id", None)
    return str(ident) if ident else None


def store_pending(hass: Any, context: Any, entity_ids: set[str] | frozenset[str]) -> None:
    """Remember which entity_ids this request may show the model."""
    ident = context_id(context)
    if not ident:
        return
    _bucket(hass)[ident] = frozenset(entity_ids)


def peek_pending(hass: Any, context: Any) -> frozenset[str] | None:
    """Return the stored ids, or None if this request did not go through the prefilter."""
    ident = context_id(context)
    if not ident:
        return None
    return hass.data.get(DOMAIN, {}).get(PENDING_FILTERS, {}).get(ident)


def clear_pending(hass: Any, context: Any) -> None:
    """Drop the stored ids after the downstream call finishes."""
    ident = context_id(context)
    if not ident:
        return
    hass.data.get(DOMAIN, {}).get(PENDING_FILTERS, {}).pop(ident, None)
