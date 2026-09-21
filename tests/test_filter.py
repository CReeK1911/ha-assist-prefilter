"""Filter scoring tests against a fake kitchen/living-room catalog."""

from assist_prefilter.catalog import Catalog, CatalogArea, CatalogEntity
from assist_prefilter.filter import filter_catalog


def _catalog() -> Catalog:
    kok = CatalogArea(
        area_id="kok",
        name="kök",
        aliases=("köket", "kok", "koket"),
    )
    vardagsrum = CatalogArea(
        area_id="vardagsrum",
        name="vardagsrum",
        aliases=("vardagsrummet",),
    )
    return Catalog(
        areas=[kok, vardagsrum],
        entities=[
            CatalogEntity(
                entity_id="light.kok_taklampa",
                name="Köks taklampa",
                domain="light",
                aliases=("kökslampa", "taklampan i köket"),
                area_id="kok",
                area_name="kök",
                area_aliases=kok.aliases,
                state="on",
            ),
            CatalogEntity(
                entity_id="light.kok_bank",
                name="Bänkbelysning",
                domain="light",
                aliases=("bänklampa",),
                area_id="kok",
                area_name="kök",
                area_aliases=kok.aliases,
                state="off",
            ),
            CatalogEntity(
                entity_id="light.vardagsrum_tv",
                name="TV-lampa",
                domain="light",
                aliases=("tv lampan",),
                area_id="vardagsrum",
                area_name="vardagsrum",
                state="on",
            ),
            CatalogEntity(
                entity_id="media_player.vardagsrum_tv",
                name="TV",
                domain="media_player",
                area_id="vardagsrum",
                area_name="vardagsrum",
                state="idle",
            ),
        ],
    )


def test_kitchen_ceiling_light_from_swedish_utterance() -> None:
    result = filter_catalog("släck taklampan i köket", _catalog())
    ids = [e.entity_id for e in result.entities]
    assert "light.kok_taklampa" in ids
    assert "light.vardagsrum_tv" not in ids
    area_ids = {a.area_id for a in result.areas}
    assert "kok" in area_ids
    assert result.entities[0].entity_id == "light.kok_taklampa"


def test_satellite_kitchen_boosts_kitchen_lights() -> None:
    result = filter_catalog(
        "släck lampan",
        _catalog(),
        satellite_area_id="kok",
    )
    ids = [e.entity_id for e in result.entities]
    assert ids == ["light.kok_taklampa"]
    assert "light.vardagsrum_tv" not in ids
    assert "media_player.vardagsrum_tv" not in ids


def test_cap_respected() -> None:
    cat = _catalog()
    extras = [
        CatalogEntity(
            entity_id=f"light.kok_extra_{i:02d}",
            name=f"Köks extra {i}",
            domain="light",
            aliases=("kökslampa",),
            area_id="kok",
            area_name="kök",
        )
        for i in range(40)
    ]
    cat.entities.extend(extras)
    result = filter_catalog(
        "släck lampan i köket",
        cat,
        max_entities=8,
        max_areas=2,
    )
    assert len(result.entities) <= 8
    assert len(result.areas) <= 2


def test_chit_chat_does_not_dump_house() -> None:
    result = filter_catalog("vad är klockan", _catalog())
    assert len(result.entities) <= 2


def _house_catalog() -> Catalog:
    """Subset of the live HA house that previously failed ranking."""
    kok = CatalogArea(area_id="kok", name="Kök", aliases=("köket", "kok", "koket"))
    barn = CatalogArea(area_id="barnrummet", name="Barnrummet")
    vardagsrum = CatalogArea(
        area_id="vardagsrum", name="Vardagsrum", aliases=("vardagsrummet",)
    )
    return Catalog(
        areas=[kok, barn, vardagsrum],
        entities=[
            CatalogEntity(
                entity_id="light.takbelysning_kok_matsal_kok",
                name="Takbelysning kök matsal - Kök",
                domain="light",
                area_id="kok",
                area_name="Kök",
                area_aliases=kok.aliases,
            ),
            CatalogEntity(
                entity_id="light.bel_koksbank_kok",
                name="Bel Köksbänk - Kök",
                domain="light",
                area_id="kok",
                area_name="Kök",
                area_aliases=kok.aliases,
            ),
            CatalogEntity(
                entity_id="light.underskaps_bel_kok_kok",
                name="Underskåps bel kök - Kök",
                domain="light",
                area_id="kok",
                area_name="Kök",
                area_aliases=kok.aliases,
            ),
            CatalogEntity(
                entity_id="light.kok_fonsterlampa_kok",
                name="Fönsterlampa kök",
                domain="light",
                area_id="kok",
                area_name="Kök",
                area_aliases=kok.aliases,
            ),
            CatalogEntity(
                entity_id="light.taklampa_barnrum_sovrum_barn",
                name="Taklampa barnrum - Sovrum barn",
                domain="light",
                area_id="barnrummet",
                area_name="Barnrummet",
            ),
            CatalogEntity(
                entity_id="light.fonster_belysning_barnrum_sovrum_barn",
                name="Fönster belysning barnrum - Sovrum barn",
                domain="light",
                area_id="barnrummet",
                area_name="Barnrummet",
            ),
            CatalogEntity(
                entity_id="switch.sonoff_barnrum_adventsljusstake_switch",
                name="SONOFF Barnrum adventsljusstake Switch",
                domain="switch",
                area_id="barnrummet",
                area_name="Barnrummet",
            ),
            CatalogEntity(
                entity_id="script.filmbelysning_kok",
                name="Filmbelysning kök",
                domain="script",
                area_id="kok",
                area_name="Kök",
                area_aliases=kok.aliases,
            ),
            CatalogEntity(
                entity_id="light.julgran",
                name="Julgran",
                domain="light",
                area_id="vardagsrum",
                area_name="Vardagsrum",
            ),
        ],
    )


def test_kitchen_ceiling_not_kids_room_taklampa() -> None:
    result = filter_catalog("släck taklampan i köket", _house_catalog())
    ids = [e.entity_id for e in result.entities]
    assert "light.takbelysning_kok_matsal_kok" in ids
    assert "light.taklampa_barnrum_sovrum_barn" not in ids
    assert result.entities[0].entity_id == "light.takbelysning_kok_matsal_kok"


def test_julgranen_matches_julgran() -> None:
    result = filter_catalog("tänd julgranen", _house_catalog())
    ids = [e.entity_id for e in result.entities]
    assert ids[0] == "light.julgran"


def test_underskaps_and_bank_rank_in_kitchen() -> None:
    cat = _house_catalog()
    under = filter_catalog("tänd underskåpsbelysningen i köket", cat)
    assert under.entities[0].entity_id == "light.underskaps_bel_kok_kok"
    bank = filter_catalog("tänd bänklampan i köket", cat)
    assert bank.entities[0].entity_id == "light.bel_koksbank_kok"


def test_fonsterlampa_in_kitchen_not_living_room() -> None:
    result = filter_catalog("släck fönsterlampan i köket", _house_catalog())
    ids = [e.entity_id for e in result.entities]
    assert ids[0] == "light.kok_fonsterlampa_kok"
    assert "light.taklampa_barnrum_sovrum_barn" not in ids


def test_lampan_i_barnrummet_is_the_ceiling_light() -> None:
    result = filter_catalog("Släck lampan i barnrummet", _house_catalog())
    assert [e.entity_id for e in result.entities] == [
        "light.taklampa_barnrum_sovrum_barn"
    ]
    assert {a.area_id for a in result.areas} == {"barnrummet"}


def test_slack_lampan_prefers_the_light_that_is_on() -> None:
    cat = _house_catalog()
    ceiling = "light.taklampa_barnrum_sovrum_barn"
    window = "light.fonster_belysning_barnrum_sovrum_barn"
    cat.entities = [
        (
            CatalogEntity(
                entity_id=entity.entity_id,
                name=entity.name,
                domain=entity.domain,
                aliases=entity.aliases,
                area_id=entity.area_id,
                area_name=entity.area_name,
                area_aliases=entity.area_aliases,
                state="off" if entity.entity_id == ceiling else "on",
            )
            if entity.entity_id in {ceiling, window}
            else entity
        )
        for entity in cat.entities
    ]
    result = filter_catalog("Släck lampan i barnrummet", cat)
    assert [entity.entity_id for entity in result.entities] == [window]


def test_tand_lampan_prefers_the_light_that_is_off() -> None:
    cat = _house_catalog()
    ceiling = "light.taklampa_barnrum_sovrum_barn"
    window = "light.fonster_belysning_barnrum_sovrum_barn"
    cat.entities = [
        CatalogEntity(
            entity_id=entity.entity_id,
            name=entity.name,
            domain=entity.domain,
            aliases=entity.aliases,
            area_id=entity.area_id,
            area_name=entity.area_name,
            area_aliases=entity.area_aliases,
            state=(
                "on"
                if entity.entity_id == ceiling
                else "off"
                if entity.entity_id == window
                else entity.state
            ),
        )
        for entity in cat.entities
    ]
    result = filter_catalog("Tänd lampan i barnrummet", cat)
    assert [entity.entity_id for entity in result.entities] == [window]


def test_slack_lamporna_keeps_every_light_that_is_on() -> None:
    cat = _house_catalog()
    ceiling = "light.taklampa_barnrum_sovrum_barn"
    window = "light.fonster_belysning_barnrum_sovrum_barn"
    cat.entities = [
        CatalogEntity(
            entity_id=entity.entity_id,
            name=entity.name,
            domain=entity.domain,
            aliases=entity.aliases,
            area_id=entity.area_id,
            area_name=entity.area_name,
            area_aliases=entity.area_aliases,
            state="on" if entity.entity_id in {ceiling, window} else entity.state,
        )
        for entity in cat.entities
    ]
    result = filter_catalog("Släck lamporna i barnrummet", cat)
    assert {entity.entity_id for entity in result.entities} == {ceiling, window}


def test_slack_when_nothing_is_on_still_returns_the_ceiling() -> None:
    cat = _house_catalog()
    ceiling = "light.taklampa_barnrum_sovrum_barn"
    window = "light.fonster_belysning_barnrum_sovrum_barn"
    cat.entities = [
        CatalogEntity(
            entity_id=entity.entity_id,
            name=entity.name,
            domain=entity.domain,
            aliases=entity.aliases,
            area_id=entity.area_id,
            area_name=entity.area_name,
            area_aliases=entity.area_aliases,
            state="off" if entity.entity_id in {ceiling, window} else entity.state,
        )
        for entity in cat.entities
    ]
    result = filter_catalog("Släck lampan i barnrummet", cat)
    assert [entity.entity_id for entity in result.entities] == [ceiling]


def test_fonsterlampa_i_barnrummet_stays_the_window() -> None:
    result = filter_catalog("Släck fönsterlampan i barnrummet", _house_catalog())
    assert result.entities[0].entity_id == "light.fonster_belysning_barnrum_sovrum_barn"


def test_fonster_in_area_name_does_not_steal_kitchen() -> None:
    cat = _house_catalog()
    cat.areas.append(
        CatalogArea(area_id="forrad_fonster", name="Förråd Fönster")
    )
    result = filter_catalog("släck fönsterlampan i köket", cat)
    assert {a.area_id for a in result.areas} == {"kok"}
