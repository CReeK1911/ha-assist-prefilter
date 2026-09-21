"""Needle JSON parsing (no HTTP / no Home Assistant)."""

from assist_prefilter.catalog import CatalogArea, CatalogEntity
from assist_prefilter.filter import FilterResult
from assist_prefilter.needle_client import _apply_selection, _parse_select_targets


def test_parse_direct_shape() -> None:
    entity_ids, areas = _parse_select_targets(
        {"entity_ids": ["light.kok_taklampa"], "areas": ["kok"], "intent": "control"}
    )
    assert entity_ids == ["light.kok_taklampa"]
    assert areas == ["kok"]


def test_parse_tool_call_shape() -> None:
    entity_ids, areas = _parse_select_targets(
        {
            "tool_calls": [
                {
                    "name": "select_targets",
                    "arguments": {
                        "entity_ids": ["light.kok_taklampa"],
                        "areas": ["kok"],
                    },
                }
            ]
        }
    )
    assert entity_ids == ["light.kok_taklampa"]
    assert areas == ["kok"]


def test_apply_selection_subsets() -> None:
    result = FilterResult(
        folded_text="slack",
        tokens=["slack"],
        expanded_tokens={"slack"},
        areas=[
            CatalogArea(area_id="kok", name="kök"),
            CatalogArea(area_id="hall", name="hall"),
        ],
        entities=[
            CatalogEntity(
                entity_id="light.kok_taklampa", name="Köks taklampa", domain="light"
            ),
            CatalogEntity(
                entity_id="light.hall", name="Hall", domain="light"
            ),
        ],
        entity_scores={"light.kok_taklampa": 5, "light.hall": 1},
    )
    refined = _apply_selection(result, ["light.kok_taklampa"], ["kok"])
    assert [e.entity_id for e in refined.entities] == ["light.kok_taklampa"]
    assert [a.area_id for a in refined.areas] == ["kok"]
