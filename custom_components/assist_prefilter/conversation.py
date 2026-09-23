"""Conversation entity that prefilters then delegates to an LLM agent."""

from __future__ import annotations

from dataclasses import replace
import logging
import time
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import entry_config
from .catalog import CatalogCache, render_from_filter, satellite_area_id
from .const import (
    action_speech,
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
    DEFAULT_MAX_AREAS,
    DEFAULT_MAX_ENTITIES,
    DEFAULT_NAME,
    DOMAIN,
    EVENT_FILTERED,
    error_speech,
)
from .filter import FilterResult, filter_catalog, utterance_is_query
from .filter_store import clear_pending, store_pending
from .normalize import expand_tokens, folded_tokens, repair_stt_command

_LOGGER = logging.getLogger(__name__)

_LOCAL_MISS = {
    intent.IntentResponseErrorCode.NO_INTENT_MATCH,
    intent.IntentResponseErrorCode.NO_VALID_TARGETS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the conversation entity for a config entry."""
    async_add_entities([AssistPrefilterEntity(hass, entry)])


def _is_local_hit(result: conversation.ConversationResult) -> bool:
    """True when built-in intents handled the utterance."""
    response = result.response
    if response.response_type != intent.IntentResponseType.ERROR:
        return True
    code = getattr(response, "error_code", None)
    if code is None and isinstance(getattr(response, "data", None), dict):
        code = response.data.get("code")
    if code in _LOCAL_MISS:
        return False
    if isinstance(code, str) and code in {item.value for item in _LOCAL_MISS}:
        return False
    return True


def _speech_from_result(result: conversation.ConversationResult) -> str:
    speech = result.response.speech or {}
    plain = speech.get("plain") or {}
    return str(plain.get("speech") or "")


class AssistPrefilterEntity(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
):
    """Pipeline conversation agent: filter, then call the user's LLM."""

    _attr_has_entity_name = False
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL
    _attr_supports_streaming = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the entity."""
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_name = entry_config(entry).get(CONF_NAME) or DEFAULT_NAME

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Language is the downstream agent's problem."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register as a conversation agent."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)
        stored = self.hass.data.setdefault(DOMAIN, {}).setdefault(
            self.entry.entry_id, {}
        )
        stored["entity_id"] = self.entity_id

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the agent."""
        conversation.async_unset_agent(self.hass, self.entry)
        stored = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id)
        if stored:
            stored["entity_id"] = None
        await super().async_will_remove_from_hass()

    def _cache(self) -> CatalogCache:
        stored = self.hass.data.setdefault(DOMAIN, {}).setdefault(
            self.entry.entry_id, {}
        )
        cache = stored.get("cache")
        if cache is None:
            cache = CatalogCache(self.hass)
            cache.async_setup()
            stored["cache"] = cache
        return cache

    def _error_result(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        key: str,
    ) -> conversation.ConversationResult:
        speech = error_speech(user_input.language, key)
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=user_input.agent_id or self.entity_id,
                content=speech,
            )
        )
        response = intent.IntentResponse(language=user_input.language or "")
        response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, speech)
        return conversation.ConversationResult(
            response=response,
            conversation_id=chat_log.conversation_id,
        )

    def _attach_local_speech(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        result: conversation.ConversationResult,
    ) -> None:
        """DefaultAgent is not a ConversationEntity; write the shared log."""
        last = chat_log.content[-1] if chat_log.content else None
        if last is not None and getattr(last, "role", None) == "assistant":
            return
        speech = _speech_from_result(result)
        if not speech:
            return
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=user_input.agent_id or self.entity_id,
                content=speech,
            )
        )

    def _fire_event(self, payload: dict[str, Any]) -> None:
        self.hass.bus.async_fire(EVENT_FILTERED, payload)

    async def _try_needle_execute(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        result: FilterResult,
        url: str,
    ) -> conversation.ConversationResult | None:
        """Run a light command when Needle's answer stays inside the filtered set."""
        from .needle_client import needle_execute

        started = time.perf_counter()
        entity_ids = await needle_execute(
            self.hass, url, user_input.text, result
        )
        if not entity_ids:
            return None
        service = "turn_on" if result.action == "turn_on" else "turn_off"
        try:
            await self.hass.services.async_call(
                "light",
                service,
                {"entity_id": entity_ids},
                blocking=True,
                context=user_input.context,
            )
        except Exception as err:
            _LOGGER.debug("Could not run Needle light command: %s", err)
            return None

        chosen = [entity for entity in result.entities if entity.entity_id in set(entity_ids)]
        if len(chosen) == 1:
            raw_name = chosen[0].name or entity_ids[0]
            speech = action_speech(
                user_input.language,
                service,
                room=None,
                name=raw_name.split(" - ", 1)[0],
            )
        else:
            room = next((entity.area_name for entity in chosen if entity.area_name), None)
            speech = action_speech(
                user_input.language, service, room=room, name=None
            )
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=user_input.agent_id or self.entity_id,
                content=speech,
            )
        )
        response = intent.IntentResponse(language=user_input.language or "")
        response.async_set_speech(speech)
        self._fire_event(
            {
                "local_intent": False,
                "needle_executed": True,
                "entity_count": len(entity_ids),
                "area_count": len(result.areas),
                "entity_ids": entity_ids,
                "area_ids": [area.area_id for area in result.areas],
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "agent_entity_id": self.entity_id,
                "is_query": False,
            }
        )
        return conversation.ConversationResult(
            response=response,
            conversation_id=chat_log.conversation_id,
        )

    async def _try_local_intents(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult | None:
        try:
            result = await conversation.async_converse(
                self.hass,
                text=user_input.text,
                conversation_id=user_input.conversation_id,
                context=user_input.context,
                language=user_input.language,
                agent_id=conversation.HOME_ASSISTANT_AGENT,
                device_id=user_input.device_id,
                satellite_id=user_input.satellite_id,
            )
        except (ValueError, intent.IntentHandleError) as err:
            _LOGGER.debug("Local intents failed: %s", err)
            return None
        if _is_local_hit(result):
            self._attach_local_speech(user_input, chat_log, result)
            return result
        return None

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Filter the house, then forward the original sentence."""
        conf = entry_config(self.entry)
        self._attr_name = conf.get(CONF_NAME) or DEFAULT_NAME
        downstream = conf.get(CONF_DOWNSTREAM_AGENT_ID)
        started = time.perf_counter()

        if not downstream:
            return self._error_result(user_input, chat_log, "not_configured")
        if downstream == self.entity_id:
            return self._error_result(user_input, chat_log, "recursion")

        repaired = repair_stt_command(user_input.text)
        if repaired != user_input.text:
            _LOGGER.debug("Repaired command wording before the LLM")
            user_input = replace(user_input, text=repaired)

        if conf.get(CONF_PREFER_LOCAL_INTENTS, True):
            local = await self._try_local_intents(user_input, chat_log)
            if local is not None:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._fire_event(
                    {
                        "local_intent": True,
                        "entity_count": 0,
                        "area_count": 0,
                        "elapsed_ms": elapsed_ms,
                        "agent_entity_id": self.entity_id,
                    }
                )
                _LOGGER.debug(
                    "Local intent hit in %s ms",
                    elapsed_ms,
                )
                return local

        expanded = expand_tokens(folded_tokens(user_input.text))
        is_query = utterance_is_query(expanded)
        exclude = set(conf.get(CONF_EXCLUDE_DOMAINS) or DEFAULT_EXCLUDE_DOMAINS)
        catalog = self._cache().get(
            exclude_domains=exclude,
            include_query_domains=is_query,
            include_state=bool(conf.get(CONF_INCLUDE_STATE, True)),
        )
        sat = satellite_area_id(
            self.hass, user_input.device_id, user_input.satellite_id
        )
        result: FilterResult = filter_catalog(
            user_input.text,
            catalog,
            satellite_area_id=sat,
            max_entities=int(conf.get(CONF_MAX_ENTITIES, DEFAULT_MAX_ENTITIES)),
            max_areas=int(conf.get(CONF_MAX_AREAS, DEFAULT_MAX_AREAS)),
        )

        if conf.get(CONF_NEEDLE_ENABLED) and conf.get(CONF_NEEDLE_URL):
            from .needle_client import needle_refine

            if result.action in {"turn_on", "turn_off"}:
                executed = await self._try_needle_execute(
                    user_input, chat_log, result, str(conf[CONF_NEEDLE_URL])
                )
                if executed is not None:
                    return executed
            else:
                result = await needle_refine(
                    self.hass,
                    str(conf[CONF_NEEDLE_URL]),
                    user_input.text,
                    result,
                )

        fallback = (
            catalog
            if conf.get(CONF_FALLBACK_TO_FULL_CATALOG) and not result.entities
            else None
        )
        visible = fallback.entities if fallback is not None else result.entities
        store_pending(self.hass, user_input.context, {e.entity_id for e in visible})
        prompt = render_from_filter(
            result,
            include_state=bool(conf.get(CONF_INCLUDE_STATE, True)),
            fallback=fallback,
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self._fire_event(
            {
                "local_intent": False,
                "entity_count": len(result.entities),
                "area_count": len(result.areas),
                "entity_ids": [e.entity_id for e in result.entities],
                "area_ids": [a.area_id for a in result.areas],
                "elapsed_ms": elapsed_ms,
                "agent_entity_id": self.entity_id,
                "is_query": result.is_query,
            }
        )
        _LOGGER.debug(
            "Filtered to %s entities / %s areas in %s ms",
            len(result.entities),
            len(result.areas),
            elapsed_ms,
        )

        try:
            return await conversation.async_converse(
                self.hass,
                text=user_input.text,
                conversation_id=user_input.conversation_id,
                context=user_input.context,
                language=user_input.language,
                agent_id=str(downstream),
                device_id=user_input.device_id,
                satellite_id=user_input.satellite_id,
                extra_system_prompt=prompt,
            )
        except ValueError:
            _LOGGER.debug("Downstream agent missing: %s", downstream)
            return self._error_result(user_input, chat_log, "missing_agent")
        finally:
            clear_pending(self.hass, user_input.context)
