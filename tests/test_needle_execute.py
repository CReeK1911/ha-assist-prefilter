"""Needle may only act on lights the prefilter already selected."""

from assist_prefilter.needle_client import accepted_light_command

ALLOWED = {
    "light.taklampa_barnrum_sovrum_barn",
    "light.fonster_belysning_barnrum_sovrum_barn",
}


def test_accepts_turn_off_inside_the_list() -> None:
    assert accepted_light_command(
        "turn_off",
        ALLOWED,
        {
            "service": "turn_off",
            "entity_ids": ["light.taklampa_barnrum_sovrum_barn"],
        },
    ) == ["light.taklampa_barnrum_sovrum_barn"]


def test_rejects_an_id_outside_the_list() -> None:
    assert (
        accepted_light_command(
            "turn_off",
            ALLOWED,
            {
                "service": "turn_off",
                "entity_ids": [
                    "light.taklampa_barnrum_sovrum_barn",
                    "light.vardagsrum_tv",
                ],
            },
        )
        is None
    )


def test_rejects_turn_on_when_the_sentence_was_turn_off() -> None:
    assert (
        accepted_light_command(
            "turn_off",
            ALLOWED,
            {
                "service": "turn_on",
                "entity_ids": ["light.taklampa_barnrum_sovrum_barn"],
            },
        )
        is None
    )


def test_rejects_empty_answer() -> None:
    assert accepted_light_command("turn_off", ALLOWED, {"service": "turn_off"}) is None
    assert accepted_light_command("query", ALLOWED, {"service": "turn_off"}) is None
