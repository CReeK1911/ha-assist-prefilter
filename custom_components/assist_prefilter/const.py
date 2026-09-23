"""Constants for Assist Prefilter."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "assist_prefilter"
PLATFORMS: Final = ["conversation"]

DEFAULT_NAME: Final = "Assist Prefilter"
DEFAULT_MAX_ENTITIES: Final = 24
DEFAULT_MAX_AREAS: Final = 8
DEFAULT_PREFER_LOCAL_INTENTS: Final = True
DEFAULT_INCLUDE_STATE: Final = True
DEFAULT_FALLBACK_TO_FULL_CATALOG: Final = False
DEFAULT_NEEDLE_ENABLED: Final = False
DEFAULT_NEEDLE_URL: Final = "http://127.0.0.1:8099/complete"
DEFAULT_NEEDLE_TIMEOUT_MS: Final = 2500

CONF_NAME: Final = "name"
CONF_DOWNSTREAM_AGENT_ID: Final = "downstream_agent_id"
CONF_PREFER_LOCAL_INTENTS: Final = "prefer_local_intents"
CONF_MAX_ENTITIES: Final = "max_entities"
CONF_MAX_AREAS: Final = "max_areas"
CONF_INCLUDE_STATE: Final = "include_state"
CONF_FALLBACK_TO_FULL_CATALOG: Final = "fallback_to_full_catalog"
CONF_EXCLUDE_DOMAINS: Final = "exclude_domains"
CONF_NEEDLE_URL: Final = "needle_url"
CONF_NEEDLE_ENABLED: Final = "needle_enabled"

EVENT_FILTERED: Final = "assist_prefilter_filtered"
SERVICE_DEBUG_FILTER: Final = "debug_filter"

# Domains the user would typically voice-control.
DEFAULT_VOICE_DOMAINS: Final = frozenset(
    {
        "light",
        "switch",
        "cover",
        "fan",
        "climate",
        "media_player",
        "lock",
        "scene",
        "script",
        "input_boolean",
        "vacuum",
        "water_heater",
        "humidifier",
        "remote",
        "alarm_control_panel",
    }
)

# Noisy unless the utterance looks like a query.
DEFAULT_EXCLUDE_DOMAINS: Final = frozenset(
    {
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
    }
)

QUERY_INCLUDE_DOMAINS: Final = frozenset(
    {"sensor", "binary_sensor", "weather", "number"}
)

# Domain cue lexicon (sv + en). Values are folded later.
DOMAIN_CUES: Final[dict[str, frozenset[str]]] = {
    "light": frozenset(
        {
            "lampa",
            "lampan",
            "lampor",
            "lamporna",
            "ljus",
            "belysning",
            "light",
            "lights",
            "lamp",
            "lamps",
            "lighting",
        }
    ),
    "switch": frozenset(
        {
            "switch",
            "switches",
            "strömbrytare",
            "brytare",
            "uttag",
        }
    ),
    "cover": frozenset(
        {
            "gardin",
            "gardinen",
            "gardiner",
            "rullgardin",
            "persienn",
            "markis",
            "cover",
            "covers",
            "blind",
            "blinds",
            "curtain",
            "curtains",
            "shutter",
            "shutters",
            "awning",
        }
    ),
    "fan": frozenset({"fläkt", "fläkten", "fan", "fans"}),
    "climate": frozenset(
        {
            "värme",
            "värmen",
            "kyla",
            "termostat",
            "temperatur",
            "climate",
            "heat",
            "heating",
            "ac",
            "thermostat",
            "temperature",
        }
    ),
    "media_player": frozenset(
        {
            "tv",
            "teve",
            "television",
            "radio",
            "musik",
            "media",
            "spotify",
            "speaker",
            "högtalare",
            "player",
        }
    ),
    "lock": frozenset({"lås", "låset", "lock", "locks", "dörr", "dörren", "door"}),
    "scene": frozenset({"scen", "scenen", "scene", "scenes"}),
    "script": frozenset({"script", "scripts", "skript"}),
    "vacuum": frozenset({"dammsugare", "robot", "vacuum", "roborock"}),
    "humidifier": frozenset({"humidifier", "luftfuktare"}),
    "remote": frozenset({"remote", "fjärrkontroll"}),
    "alarm_control_panel": frozenset({"alarm", "larm", "alarmet"}),
}

QUERY_CUES: Final = frozenset(
    {
        "hur",
        "vad",
        "vilken",
        "vilket",
        "vilka",
        "temperatur",
        "status",
        "how",
        "what",
        "which",
        "temperature",
        "temp",
        "varmt",
        "varm",
        "kallt",
        "kall",
        "grader",
        "fuktigt",
        "öppet",
        "öppen",
        "stängd",
    }
)

ACTION_STOPWORDS: Final = frozenset(
    {
        "i",
        "the",
        "a",
        "an",
        "on",
        "off",
        "to",
        "in",
        "at",
        "of",
        "and",
        "or",
        "for",
        "my",
        "me",
        "please",
        "på",
        "av",
        "och",
        "att",
        "en",
        "ett",
        "den",
        "det",
        "de",
        "du",
        "jag",
        "kan",
        "vill",
        "ska",
        "släck",
        "tänd",
        "stäng",
        "öppna",
        "sätt",
        "turn",
        "switch",
        "put",
        "set",
        "dim",
        "close",
        "open",
        "start",
        "stop",
    }
)

PRIMARY_DOMAINS: Final = frozenset(
    {"light", "switch", "cover", "fan", "climate", "lock", "media_player"}
)

ERROR_SPEECH: Final[dict[str, dict[str, str]]] = {
    "en": {
        "not_configured": (
            "Assist Prefilter is not configured. "
            "Pick a downstream conversation agent."
        ),
        "missing_agent": "The downstream conversation agent is unavailable.",
        "recursion": "Assist Prefilter cannot use itself as the downstream agent.",
    },
    "sv": {
        "not_configured": (
            "Assist Prefilter är inte konfigurerad. "
            "Välj en nedströms konversationsagent."
        ),
        "missing_agent": "Nedströms konversationsagent saknas eller är otillgänglig.",
        "recursion": "Assist Prefilter kan inte använda sig själv som nedströms agent.",
    },
}


def error_speech(language: str | None, key: str) -> str:
    """Return a short spoken error in the user's language."""
    lang = (language or "en").lower()
    table = ERROR_SPEECH["sv"] if lang.startswith("sv") else ERROR_SPEECH["en"]
    return table.get(key, ERROR_SPEECH["en"]["missing_agent"])
