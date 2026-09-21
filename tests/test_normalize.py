"""Fold / definite-form tests. No Home Assistant required."""

from assist_prefilter.normalize import expand_token, fold, fold_match, folded_tokens


def test_fold_swedish_variants_match_slug() -> None:
    """kök / köket / Koket / kok all fold-match."""
    variants = ["kök", "köket", "Koket", "kok", "KÖK"]
    folded = {fold(v) for v in variants}
    # köket / Koket keep the definite -et; kök / kok collapse to kok.
    assert fold("kök") == "kok"
    assert fold("kok") == "kok"
    assert fold("köket") == "koket"
    assert fold("Koket") == "koket"
    assert fold("Köket") == "koket"
    # Shared stem via expand
    stems = set()
    for v in variants:
        stems |= expand_token(v)
    assert "kok" in stems
    assert folded  # sanity


def test_fold_match_kitchen() -> None:
    assert fold_match("kök", "kok")
    assert fold_match("köket", "kök")
    assert fold_match("Koket", "kök")
    assert fold_match("kök", "köket")


def test_sovrum_does_not_match_kok() -> None:
    assert not fold_match("sovrum", "kök")
    assert "kok" not in expand_token("sovrum")
    assert fold("sovrum") == "sovrum"


def test_taklampan_stems_to_taklampa() -> None:
    expanded = expand_token("taklampan")
    assert "taklampa" in expanded
    assert "taklampan" in expanded


def test_folded_tokens_splits_sentence() -> None:
    tokens = folded_tokens("Släck taklampan i köket")
    assert "taklampan" in tokens
    assert "koket" in tokens
    assert "slack" in tokens


def test_entity_id_style_underscores() -> None:
    from assist_prefilter.normalize import entity_id_tokens

    tokens = entity_id_tokens("light.kok_taklampa")
    assert "kok" in tokens
    assert "taklampa" in tokens
