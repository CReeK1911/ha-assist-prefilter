"""Context-block rendering tests."""

from assist_prefilter.catalog import (
    Catalog,
    CatalogArea,
    CatalogEntity,
    render_context_block,
    render_from_filter,
)
from assist_prefilter.filter import filter_catalog


def _small_catalog() -> Catalog:
    area = CatalogArea(area_id="kok", name="kök", aliases=("köket", "kok", "koket"))
    return Catalog(
        areas=[area],
        entities=[
            CatalogEntity(
                entity_id="light.kok_taklampa",
                name="Köks taklampa",
                domain="light",
                aliases=("kökslampa", "taklampan i köket"),
                area_id="kok",
                area_name="kök",
                state="on",
            )
        ],
    )


def test_block_contains_entity_id_and_spoken_name() -> None:
    cat = _small_catalog()
    result = filter_catalog("släck taklampan i köket", cat)
    block = render_from_filter(result, include_state=True)
    assert "light.kok_taklampa" in block
    assert "Köks taklampa" in block
    assert "kök" in block.lower() or "kök" in block
    assert "entity_id is the only valid id" in block


def test_not_a_200_entity_dump() -> None:
    area = CatalogArea(area_id="kok", name="kök", aliases=("köket",))
    entities = [
        CatalogEntity(
            entity_id=f"light.other_{i}",
            name=f"Other {i}",
            domain="light",
            area_id="other",
            area_name="other",
        )
        for i in range(200)
    ]
    entities.append(
        CatalogEntity(
            entity_id="light.kok_taklampa",
            name="Köks taklampa",
            domain="light",
            aliases=("kökslampa",),
            area_id="kok",
            area_name="kök",
        )
    )
    cat = Catalog(areas=[area], entities=entities)
    result = filter_catalog("släck taklampan i köket", cat)
    block = render_from_filter(result)
    assert "light.kok_taklampa" in block
    assert block.count("light.other_") < 5
    assert "light.other_199" not in block


def test_zero_hit_does_not_include_full_catalog() -> None:
    cat = _small_catalog()
    result = filter_catalog("berätta ett skämt", cat)
    block = render_from_filter(result, fallback=None)
    assert "light.kok_taklampa" not in block
    assert "Do not guess entity_ids" in block
    assert "no matching devices" in block.lower() or "No matching devices" in block


def test_infer_area_from_entity_id_token() -> None:
    from assist_prefilter.catalog import infer_area_id

    assert infer_area_id("script.filmbelysning_kok", {"kok", "vardagsrum"}) == "kok"
    assert infer_area_id("light.julgran", {"kok", "vardagsrum"}) is None


def test_render_direct_includes_state() -> None:
    cat = _small_catalog()
    block = render_context_block(cat.areas, cat.entities, include_state=True)
    assert "state=on" in block
    assert "light.kok_taklampa" in block
