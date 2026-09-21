"""Prompt shrinking and the pending-filter store. No Home Assistant required."""

from assist_prefilter.filter_store import (
    clear_pending,
    peek_pending,
    store_pending,
)
from assist_prefilter.llm_api import (
    _apply_live_filters,
    replace_static_yaml,
    restrict_exposed,
)


class _Context:
    def __init__(self, ident: str) -> None:
        self.id = ident


class _Hass:
    def __init__(self) -> None:
        self.data: dict = {}


def test_replace_static_yaml_swaps_only_the_dump() -> None:
    full = "- names: Köket\n  domain: light\n"
    filtered = "- names: Taklampa\n  domain: light\n"
    prompt = (
        "When controlling Home Assistant always call the intent tools.\n"
        "Static Context: An overview of the areas and the devices in this smart home:\n"
        f"{full}"
        "Other instructions stay."
    )
    shrunk = replace_static_yaml(prompt, full, filtered)
    assert filtered in shrunk
    assert full not in shrunk
    assert "always call the intent tools" in shrunk
    assert "Other instructions stay." in shrunk


def test_replace_is_noop_when_yaml_is_absent() -> None:
    prompt = "no static dump here"
    assert replace_static_yaml(prompt, "- names: x\n", "[]\n") == prompt


def test_restrict_exposed_keeps_hits_only() -> None:
    exposed = {
        "light.kok_tak": {"names": "Tak", "domain": "light"},
        "light.barn": {"names": "Barn", "domain": "light"},
    }
    kept = restrict_exposed(exposed, frozenset({"light.kok_tak"}))
    assert list(kept) == ["light.kok_tak"]


def test_pending_filter_roundtrip_including_empty() -> None:
    hass = _Hass()
    ctx = _Context("ctx-1")
    assert peek_pending(hass, ctx) is None
    store_pending(hass, ctx, set())
    assert peek_pending(hass, ctx) == frozenset()
    store_pending(hass, ctx, {"light.kok_tak"})
    assert peek_pending(hass, ctx) == frozenset({"light.kok_tak"})
    clear_pending(hass, ctx)
    assert peek_pending(hass, ctx) is None


def test_live_filter_cannot_widen_the_set() -> None:
    exposed = {
        "light.kok_tak": {"names": "Taklampa kök", "domain": "light", "areas": "Kök"},
        "sensor.kok_temp": {
            "names": "Temperatur kök",
            "domain": "sensor",
            "areas": "Kök",
        },
    }

    class _Input:
        tool_args = {"domain": "light"}

    kept = _apply_live_filters(exposed, _Input())
    assert list(kept) == ["light.kok_tak"]
