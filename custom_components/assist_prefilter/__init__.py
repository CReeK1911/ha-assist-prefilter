"""Assist Prefilter — conversation agent that shrinks Assist context."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import (
    CONF_DOWNSTREAM_AGENT_ID,
    CONF_EXCLUDE_DOMAINS,
    CONF_FALLBACK_TO_FULL_CATALOG,
    CONF_INCLUDE_STATE,
    CONF_MAX_AREAS,
    CONF_MAX_ENTITIES,
    CONF_NAME,
    CONF_NEEDLE_ENABLED,
    CONF_NEEDLE_URL,
    CONF_PREFER_LOCAL_INTENTS,
    DEFAULT_EXCLUDE_DOMAINS,
    DEFAULT_FALLBACK_TO_FULL_CATALOG,
    DEFAULT_INCLUDE_STATE,
    DEFAULT_MAX_AREAS,
    DEFAULT_MAX_ENTITIES,
    DEFAULT_NAME,
    DEFAULT_NEEDLE_ENABLED,
    DEFAULT_NEEDLE_URL,
    DEFAULT_PREFER_LOCAL_INTENTS,
    DOMAIN,
    PLATFORMS,
    SERVICE_DEBUG_FILTER,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers.typing import ConfigType

try:
    from homeassistant.helpers import config_validation as cv
except ImportError:
    CONFIG_SCHEMA: Any = {}
else:
    CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def entry_config(entry: ConfigEntry) -> dict[str, Any]:
    """Merge defaults, data, and options."""
    return {
        CONF_NAME: DEFAULT_NAME,
        CONF_DOWNSTREAM_AGENT_ID: None,
        CONF_PREFER_LOCAL_INTENTS: DEFAULT_PREFER_LOCAL_INTENTS,
        CONF_MAX_ENTITIES: DEFAULT_MAX_ENTITIES,
        CONF_MAX_AREAS: DEFAULT_MAX_AREAS,
        CONF_INCLUDE_STATE: DEFAULT_INCLUDE_STATE,
        CONF_FALLBACK_TO_FULL_CATALOG: DEFAULT_FALLBACK_TO_FULL_CATALOG,
        CONF_EXCLUDE_DOMAINS: list(DEFAULT_EXCLUDE_DOMAINS),
        CONF_NEEDLE_URL: DEFAULT_NEEDLE_URL,
        CONF_NEEDLE_ENABLED: DEFAULT_NEEDLE_ENABLED,
        **dict(entry.data),
        **dict(entry.options),
    }


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the debug service once."""
    from homeassistant.core import SupportsResponse
    from homeassistant.helpers import config_validation as cv
    import voluptuous as vol

    from .catalog import CatalogCache, render_from_filter, satellite_area_id
    from .filter import filter_catalog, utterance_is_query
    from .normalize import expand_tokens, fold, folded_tokens

    hass.data.setdefault(DOMAIN, {})
    from .llm_api import async_register_filtered_api

    async_register_filtered_api(hass)
    if hass.services.has_service(DOMAIN, SERVICE_DEBUG_FILTER):
        return True

    async def _debug_filter(call: ServiceCall) -> dict[str, Any]:
        text: str = call.data["text"]
        agent_entity_id: str | None = call.data.get("agent_entity_id")
        entry: ConfigEntry | None = None
        if agent_entity_id:
            for item in hass.config_entries.async_entries(DOMAIN):
                entity_id = hass.data[DOMAIN].get(item.entry_id, {}).get("entity_id")
                if entity_id == agent_entity_id:
                    entry = item
                    break
        if entry is None:
            entries = hass.config_entries.async_entries(DOMAIN)
            entry = entries[0] if entries else None
        if entry is None:
            return {"error": "no_config_entry", "folded_text": fold(text)}

        conf = entry_config(entry)
        stored = hass.data[DOMAIN].setdefault(entry.entry_id, {})
        cache: CatalogCache | None = stored.get("cache")
        if cache is None:
            cache = CatalogCache(hass)
            cache.async_setup()
            stored["cache"] = cache

        expanded = expand_tokens(folded_tokens(text))
        is_query = utterance_is_query(expanded)
        exclude = set(conf.get(CONF_EXCLUDE_DOMAINS) or DEFAULT_EXCLUDE_DOMAINS)
        catalog = cache.get(
            exclude_domains=exclude,
            include_query_domains=is_query,
            include_state=bool(conf.get(CONF_INCLUDE_STATE, True)),
        )
        sat = satellite_area_id(hass, None, None)
        result = filter_catalog(
            text,
            catalog,
            satellite_area_id=sat,
            max_entities=int(conf.get(CONF_MAX_ENTITIES, DEFAULT_MAX_ENTITIES)),
            max_areas=int(conf.get(CONF_MAX_AREAS, DEFAULT_MAX_AREAS)),
        )
        fallback = (
            catalog
            if conf.get(CONF_FALLBACK_TO_FULL_CATALOG, False) and not result.entities
            else None
        )
        block = render_from_filter(
            result,
            include_state=bool(conf.get(CONF_INCLUDE_STATE, True)),
            fallback=fallback,
        )
        return {
            "folded_text": result.folded_text,
            "tokens": result.tokens,
            "expanded_tokens": sorted(result.expanded_tokens),
            "matched_areas": [
                {"area_id": a.area_id, "name": a.name} for a in result.areas
            ],
            "scored_entities": [
                {
                    "entity_id": e.entity_id,
                    "name": e.name,
                    "score": result.entity_scores.get(e.entity_id, 0),
                    "area": e.area_name,
                }
                for e in result.entities
            ],
            "context_block": block,
            "is_query": result.is_query,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_DEBUG_FILTER,
        _debug_filter,
        schema=vol.Schema(
            {
                vol.Required("text"): cv.string,
                vol.Optional("agent_entity_id"): cv.entity_id,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one prefilter conversation entity."""
    from .catalog import CatalogCache

    hass.data.setdefault(DOMAIN, {})
    stored = hass.data[DOMAIN].setdefault(entry.entry_id, {})
    if stored.get("cache") is None:
        cache = CatalogCache(hass)
        cache.async_setup()
        stored["cache"] = cache
    stored.setdefault("entity_id", None)
    if not hass.services.has_service(DOMAIN, SERVICE_DEBUG_FILTER):
        await async_setup(hass, {})
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    stored = hass.data[DOMAIN].pop(entry.entry_id, None)
    if stored and (cache := stored.get("cache")):
        cache.async_unload()
    return unload_ok
