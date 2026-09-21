"""Config and options flows for Assist Prefilter."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
import voluptuous as vol

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
)

EXCLUDE_DOMAIN_OPTIONS = [
    "sensor",
    "binary_sensor",
    "update",
    "button",
    "event",
    "person",
    "device_tracker",
    "zone",
    "weather",
    "number",
    "select",
    "image",
    "camera",
    "automation",
    "group",
]


def _agent_schema(
    hass: HomeAssistant,
    exclude_entity_ids: list[str] | None = None,
    default_agent: str | None = None,
) -> dict[Any, Any]:
    """Entity picker, or a string field if no conversation entities exist yet."""
    existing = hass.states.async_entity_ids("conversation")
    usable = [eid for eid in existing if eid not in (exclude_entity_ids or [])]
    if usable:
        selector: Any = EntitySelector(
            EntitySelectorConfig(
                domain="conversation",
                exclude_entities=exclude_entity_ids or [],
            )
        )
        key: Any
        if default_agent:
            key = vol.Required(CONF_DOWNSTREAM_AGENT_ID, default=default_agent)
        else:
            key = vol.Required(CONF_DOWNSTREAM_AGENT_ID)
        return {key: selector}
    if default_agent:
        return {
            vol.Required(CONF_DOWNSTREAM_AGENT_ID, default=default_agent): TextSelector()
        }
    return {vol.Required(CONF_DOWNSTREAM_AGENT_ID): TextSelector()}


def _validate_agent(
    hass: HomeAssistant, agent_id: str, self_entity_ids: list[str]
) -> str | None:
    """Return an error key, or None if the agent is acceptable."""
    if not agent_id or "." not in agent_id:
        return "invalid_agent"
    if agent_id in self_entity_ids:
        return "recursion"
    domain = agent_id.split(".", 1)[0]
    if domain != "conversation":
        return "invalid_agent"
    state = hass.states.get(agent_id)
    if state is None and hass.states.async_entity_ids("conversation"):
        # Allow a typed id that is not up yet (first-run race).
        known = hass.states.async_entity_ids("conversation")
        if agent_id not in known:
            return "invalid_agent"
    return None


class AssistPrefilterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup: name, downstream agent, caps."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            agent_id = str(user_input[CONF_DOWNSTREAM_AGENT_ID]).strip()
            err = _validate_agent(self.hass, agent_id, [])
            if err:
                errors["base"] = err
            else:
                name = str(user_input.get(CONF_NAME) or DEFAULT_NAME).strip() or DEFAULT_NAME
                max_entities = int(
                    user_input.get(CONF_MAX_ENTITIES, DEFAULT_MAX_ENTITIES)
                )
                max_areas = int(user_input.get(CONF_MAX_AREAS, DEFAULT_MAX_AREAS))
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_NAME: name,
                        CONF_DOWNSTREAM_AGENT_ID: agent_id,
                        CONF_MAX_ENTITIES: max_entities,
                        CONF_MAX_AREAS: max_areas,
                    },
                    options={
                        CONF_MAX_ENTITIES: max_entities,
                        CONF_MAX_AREAS: max_areas,
                        CONF_PREFER_LOCAL_INTENTS: DEFAULT_PREFER_LOCAL_INTENTS,
                        CONF_INCLUDE_STATE: DEFAULT_INCLUDE_STATE,
                        CONF_FALLBACK_TO_FULL_CATALOG: DEFAULT_FALLBACK_TO_FULL_CATALOG,
                        CONF_EXCLUDE_DOMAINS: list(DEFAULT_EXCLUDE_DOMAINS),
                        CONF_NEEDLE_URL: DEFAULT_NEEDLE_URL,
                        CONF_NEEDLE_ENABLED: DEFAULT_NEEDLE_ENABLED,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): TextSelector(),
                **_agent_schema(self.hass),
                vol.Required(
                    CONF_MAX_ENTITIES, default=DEFAULT_MAX_ENTITIES
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=8, max=80, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_MAX_AREAS, default=DEFAULT_MAX_AREAS
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=20, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        """Return the options flow (do not stash config_entry on the flow)."""
        return AssistPrefilterOptionsFlow()


class AssistPrefilterOptionsFlow(OptionsFlow):
    """Tune filter caps, local intents, Needle, exclude domains."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        from . import entry_config

        current = entry_config(self.config_entry)
        own_entity_id = (
            self.hass.data.get(DOMAIN, {})
            .get(self.config_entry.entry_id, {})
            .get("entity_id")
        )
        exclude_ids = [own_entity_id] if own_entity_id else []

        errors: dict[str, str] = {}
        if user_input is not None:
            agent_id = str(user_input[CONF_DOWNSTREAM_AGENT_ID]).strip()
            err = _validate_agent(self.hass, agent_id, exclude_ids)
            if err:
                errors["base"] = err
            else:
                exclude_domains = user_input.get(CONF_EXCLUDE_DOMAINS)
                if isinstance(exclude_domains, str):
                    exclude_domains = [exclude_domains]
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_NAME: str(
                            user_input.get(CONF_NAME) or current[CONF_NAME]
                        ).strip()
                        or DEFAULT_NAME,
                        CONF_DOWNSTREAM_AGENT_ID: agent_id,
                        CONF_PREFER_LOCAL_INTENTS: bool(
                            user_input.get(
                                CONF_PREFER_LOCAL_INTENTS, DEFAULT_PREFER_LOCAL_INTENTS
                            )
                        ),
                        CONF_MAX_ENTITIES: int(
                            user_input.get(CONF_MAX_ENTITIES, DEFAULT_MAX_ENTITIES)
                        ),
                        CONF_MAX_AREAS: int(
                            user_input.get(CONF_MAX_AREAS, DEFAULT_MAX_AREAS)
                        ),
                        CONF_INCLUDE_STATE: bool(
                            user_input.get(CONF_INCLUDE_STATE, DEFAULT_INCLUDE_STATE)
                        ),
                        CONF_FALLBACK_TO_FULL_CATALOG: bool(
                            user_input.get(
                                CONF_FALLBACK_TO_FULL_CATALOG,
                                DEFAULT_FALLBACK_TO_FULL_CATALOG,
                            )
                        ),
                        CONF_EXCLUDE_DOMAINS: list(
                            exclude_domains or DEFAULT_EXCLUDE_DOMAINS
                        ),
                        CONF_NEEDLE_URL: str(
                            user_input.get(CONF_NEEDLE_URL) or DEFAULT_NEEDLE_URL
                        ).strip(),
                        CONF_NEEDLE_ENABLED: bool(
                            user_input.get(CONF_NEEDLE_ENABLED, DEFAULT_NEEDLE_ENABLED)
                        ),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=current[CONF_NAME]): TextSelector(),
                **_agent_schema(
                    self.hass,
                    exclude_entity_ids=exclude_ids,
                    default_agent=current.get(CONF_DOWNSTREAM_AGENT_ID),
                ),
                vol.Required(
                    CONF_PREFER_LOCAL_INTENTS,
                    default=current[CONF_PREFER_LOCAL_INTENTS],
                ): BooleanSelector(),
                vol.Required(
                    CONF_MAX_ENTITIES, default=current[CONF_MAX_ENTITIES]
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=8, max=80, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_MAX_AREAS, default=current[CONF_MAX_AREAS]
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=20, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_INCLUDE_STATE, default=current[CONF_INCLUDE_STATE]
                ): BooleanSelector(),
                vol.Required(
                    CONF_FALLBACK_TO_FULL_CATALOG,
                    default=current[CONF_FALLBACK_TO_FULL_CATALOG],
                ): BooleanSelector(),
                vol.Optional(
                    CONF_EXCLUDE_DOMAINS,
                    default=list(current.get(CONF_EXCLUDE_DOMAINS) or DEFAULT_EXCLUDE_DOMAINS),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=EXCLUDE_DOMAIN_OPTIONS,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_NEEDLE_URL, default=current[CONF_NEEDLE_URL]
                ): TextSelector(),
                vol.Required(
                    CONF_NEEDLE_ENABLED, default=current[CONF_NEEDLE_ENABLED]
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
