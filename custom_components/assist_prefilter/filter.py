"""Deterministic catalog filter. No Home Assistant I/O."""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import Catalog, CatalogArea, CatalogEntity
from .const import (
    ACTION_STOPWORDS,
    DEFAULT_MAX_AREAS,
    DEFAULT_MAX_ENTITIES,
    DOMAIN_CUES,
    PRIMARY_DOMAINS,
    QUERY_CUES,
    QUERY_INCLUDE_DOMAINS,
)
from .normalize import (
    entity_id_tokens,
    expand_token,
    expand_tokens,
    fold,
    folded_tokens,
    generated_aliases,
)

# Fold domain-cue words once.
_FOLDED_DOMAIN_CUES: dict[str, frozenset[str]] = {
    domain: frozenset(fold(word) for word in words)
    for domain, words in DOMAIN_CUES.items()
}
_FOLDED_QUERY_CUES = frozenset(fold(w) for w in QUERY_CUES)
_FOLDED_STOPWORDS = frozenset(fold(w) for w in ACTION_STOPWORDS)
# "släck lampan i barnrummet" means the main ceiling light, not the window light.
_SINGULAR_LAMP_WORDS = frozenset(
    fold(word)
    for word in ("lampa", "lampan", "ljus", "ljuset", "light", "lamp")
)
_FIXTURE_WORDS = frozenset(
    fold(word)
    for word in (
        "tak",
        "taklampa",
        "takbelysning",
        "takljus",
        "fönster",
        "fonster",
        "fönsterlampa",
        "bänk",
        "bank",
        "bänklampa",
        "säng",
        "sang",
        "hörn",
        "horn",
        "julgran",
        "matplats",
        "spegel",
        "dusch",
        "advent",
        "underskåp",
        "underskap",
        "klot",
        "slinga",
    )
)
_CEILING_WORDS = frozenset(fold(word) for word in ("taklampa", "takbelysning", "takljus"))
# "släck" wants lights that are still on. "tänd" wants lights that are off.
_TURN_OFF_WORDS = frozenset(fold(word) for word in ("släck", "släcka", "off"))
_TURN_ON_WORDS = frozenset(fold(word) for word in ("tänd", "tända", "on"))
_DOMAIN_CUE_WORDS = frozenset(
    word for cues in _FOLDED_DOMAIN_CUES.values() for word in cues
)


@dataclass
class FilterResult:
    """Output of filter_catalog, also used by the debug service."""

    folded_text: str
    tokens: list[str]
    expanded_tokens: set[str]
    areas: list[CatalogArea] = field(default_factory=list)
    entities: list[CatalogEntity] = field(default_factory=list)
    area_scores: dict[str, int] = field(default_factory=dict)
    entity_scores: dict[str, int] = field(default_factory=dict)
    satellite_area_id: str | None = None
    is_query: bool = False


def utterance_is_query(expanded_tokens: set[str]) -> bool:
    """True when the utterance looks like a status / sensor question."""
    return bool(expanded_tokens & _FOLDED_QUERY_CUES)


def _domain_cues_in_utterance(expanded_tokens: set[str]) -> set[str]:
    domains: set[str] = set()
    for domain, cues in _FOLDED_DOMAIN_CUES.items():
        if expanded_tokens & cues:
            domains.add(domain)
    return domains


def _area_fold_tokens(ent: CatalogEntity) -> set[str]:
    """Tokens that only identify the area, not the device."""
    bits: list[str] = []
    if ent.area_id:
        bits.append(ent.area_id)
    if ent.area_name:
        bits.append(ent.area_name)
    bits.extend(ent.area_aliases)
    tokens: list[str] = []
    for bit in bits:
        tokens.extend(folded_tokens(bit.replace("_", " ")))
    return expand_tokens(tokens)


def _name_tokens(ent: CatalogEntity) -> set[str]:
    """Device name / alias / object-id tokens, excluding the area slug."""
    bits = [ent.name, *ent.aliases, *generated_aliases(ent.name)]
    tokens: list[str] = []
    for bit in bits:
        tokens.extend(folded_tokens(bit))
    tokens.extend(entity_id_tokens(ent.entity_id))
    return expand_tokens(tokens) - _area_fold_tokens(ent)


def _area_tokens(area: CatalogArea) -> set[str]:
    bits = [area.name, *area.aliases, *generated_aliases(area.name)]
    tokens: list[str] = []
    for bit in bits:
        tokens.extend(folded_tokens(bit))
    tokens.append(fold(area.area_id))
    return expand_tokens(tokens)


def _specific_name_tokens(expanded_tokens: set[str], cue_domains: set[str]) -> set[str]:
    """Tokens that look like a device/area name, not an action or domain cue."""
    cue_words: set[str] = set(_FOLDED_STOPWORDS)
    for domain in cue_domains or _FOLDED_DOMAIN_CUES:
        cue_words.update(_FOLDED_DOMAIN_CUES.get(domain, ()))
    # Always strip generic domain cues, even if none fired.
    for cues in _FOLDED_DOMAIN_CUES.values():
        cue_words.update(cues)
    return {tok for tok in expanded_tokens if tok not in cue_words and len(tok) > 1}


def _score_areas(
    catalog: Catalog, expanded_tokens: set[str]
) -> dict[str, int]:
    scores: dict[str, int] = {}
    for area in catalog.areas:
        overlap = _area_tokens(area) & expanded_tokens
        if overlap:
            scores[area.area_id] = 2 * len(overlap)
    return scores


def _score_entity(
    ent: CatalogEntity,
    expanded_tokens: set[str],
    spoken_area_ids: set[str],
    satellite_area_id: str | None,
    cue_domains: set[str],
) -> tuple[int, bool]:
    """Return (score, has_specific_name_overlap)."""
    name_toks = _name_tokens(ent)
    name_overlap = name_toks & expanded_tokens
    specific_overlap = _specific_name_tokens(name_overlap, cue_domains)
    specific = bool(specific_overlap)
    score = 0
    if name_overlap:
        score += 3
        # Prefer the ceiling light over every other kitchen lamp.
        score += min(len(specific_overlap), 3)
    if ent.area_id and ent.area_id in spoken_area_ids:
        score += 2
    if ent.domain in cue_domains:
        score += 1
    if satellite_area_id and ent.area_id == satellite_area_id:
        score += 1
    return score, specific or bool(name_overlap - _DOMAIN_CUE_WORDS)


def _fixture_words_in(text: str) -> set[str]:
    found: set[str] = set()
    for token in folded_tokens(text):
        found |= expand_token(token) & _FIXTURE_WORDS
    return found


def _generic_singular_lamp(text: str) -> bool:
    """True for "lampan"/"ljuset" without a fixture word like tak or fönster."""
    raw = set(folded_tokens(text))
    if not raw & _SINGULAR_LAMP_WORDS:
        return False
    for token in raw:
        if expand_token(token) & _FIXTURE_WORDS:
            return False
    return True


def _is_ceiling_light(entity: CatalogEntity) -> bool:
    return bool(_name_tokens(entity) & _CEILING_WORDS)


def _actionable_state(text: str) -> str | None:
    """State a light should have now for this command to still do something.

    "släck" → "on". "tänd" → "off". None when the sentence is not that command.
    """
    raw = set(folded_tokens(text))
    turn_off = bool(raw & _TURN_OFF_WORDS)
    turn_on = bool(raw & _TURN_ON_WORDS)
    if turn_off and not turn_on:
        return "on"
    if turn_on and not turn_off:
        return "off"
    return None


def _in_state(entity: CatalogEntity, state: str) -> bool:
    return (entity.state or "").casefold() == state


def _primary_entities(
    catalog: Catalog,
    area_ids: set[str],
    cue_domains: set[str],
) -> list[CatalogEntity]:
    domains = cue_domains & PRIMARY_DOMAINS if cue_domains else PRIMARY_DOMAINS
    if not domains:
        domains = PRIMARY_DOMAINS
    return [
        ent
        for ent in catalog.entities
        if ent.area_id in area_ids and ent.domain in domains
    ]


def filter_catalog(
    text: str,
    catalog: Catalog,
    *,
    satellite_area_id: str | None = None,
    max_entities: int = DEFAULT_MAX_ENTITIES,
    max_areas: int = DEFAULT_MAX_AREAS,
) -> FilterResult:
    """Score areas and entities; return a capped, stable-sorted subset."""
    tokens = folded_tokens(text)
    expanded = expand_tokens(tokens)
    # Also expand each raw token individually in case tokenize split oddly.
    for tok in tokens:
        expanded.update(expand_token(tok))

    is_query = utterance_is_query(expanded)
    cue_domains = _domain_cues_in_utterance(expanded)
    area_scores = _score_areas(catalog, expanded)
    # Keep the strongest area match only. "fönsterlampan i köket" must not
    # also select areas whose name merely contains "fönster".
    spoken_area_ids: set[str] = set()
    if area_scores:
        best = max(area_scores.values())
        if best >= 2:
            spoken_area_ids = {
                area_id for area_id, score in area_scores.items() if score == best
            }

    # Satellite area is a hint, not a spoken match, unless already scored.
    if satellite_area_id and satellite_area_id not in area_scores:
        area_scores[satellite_area_id] = 1

    entity_scores: dict[str, int] = {}
    for ent in catalog.entities:
        score, _specific = _score_entity(
            ent, expanded, spoken_area_ids, satellite_area_id, cue_domains
        )
        if is_query and ent.domain in QUERY_INCLUDE_DOMAINS:
            if ent.area_id in spoken_area_ids or (
                satellite_area_id and ent.area_id == satellite_area_id
            ):
                score += 3
        if score > 0:
            entity_scores[ent.entity_id] = score

    entity_by_id = {ent.entity_id: ent for ent in catalog.entities}
    area_by_id = {area.area_id: area for area in catalog.areas}

    selected_ids = set(entity_scores)

    # If the user named an area, stay in that area. Other-room name hits
    # ("taklampa" in barnrum while saying "i köket") must not leak in.
    if spoken_area_ids:
        selected_ids = {
            eid
            for eid in selected_ids
            if entity_by_id[eid].area_id in spoken_area_ids
        }

    # Vague command on a satellite ("släck lampan"): stay in that area.
    specific_name_in_utterance = _specific_name_tokens(expanded, cue_domains)
    if (
        satellite_area_id
        and not spoken_area_ids
        and not specific_name_in_utterance
    ):
        selected_ids = {
            eid
            for eid in selected_ids
            if entity_by_id[eid].area_id == satellite_area_id
        }

    # Area matched but no entity: include that area's primary devices.
    if spoken_area_ids and not selected_ids:
        for ent in _primary_entities(catalog, spoken_area_ids, cue_domains):
            selected_ids.add(ent.entity_id)
            entity_scores.setdefault(ent.entity_id, 2)

    if (
        satellite_area_id
        and not spoken_area_ids
        and not selected_ids
    ):
        for ent in _primary_entities(catalog, {satellite_area_id}, cue_domains):
            selected_ids.add(ent.entity_id)
            entity_scores.setdefault(ent.entity_id, 1)

    # "lampan" is a light. Do not also hand the model switches in that room.
    if "light" in cue_domains:
        lights = {
            eid
            for eid in selected_ids
            if entity_by_id[eid].domain == "light"
        }
        if lights:
            selected_ids = lights

    # "fönsterlampan" must not lose to the ceiling light just because both
    # contain "lampa". Keep entities that match the named fixture.
    fixture_words = _fixture_words_in(text)
    if fixture_words:
        matched = {
            eid
            for eid in selected_ids
            if _name_tokens(entity_by_id[eid]) & fixture_words
        }
        if matched:
            selected_ids = matched

    # "släck" keeps lights that are on. "tänd" keeps lights that are off.
    # If every candidate is already in the other state, keep them so the
    # model can say so instead of being handed an empty list.
    actionable = _actionable_state(text)
    state_narrowed = False
    if actionable and "light" in cue_domains:
        matching = {
            eid
            for eid in selected_ids
            if _in_state(entity_by_id[eid], actionable)
        }
        if matching:
            selected_ids = matching
            state_narrowed = True

    if not state_narrowed and not fixture_words and _generic_singular_lamp(text):
        # "lampan" with no fixture is the ceiling light when the room has one.
        # Otherwise the window light sorts first and the assistant picks that.
        ceilings = {
            eid for eid in selected_ids if _is_ceiling_light(entity_by_id[eid])
        }
        if ceilings:
            selected_ids = ceilings

    ranked_entities = sorted(
        (entity_by_id[eid] for eid in selected_ids if eid in entity_by_id),
        key=lambda e: (-entity_scores.get(e.entity_id, 0), e.entity_id),
    )[: max(1, max_entities)]

    # Areas to report: spoken matches first, then areas of kept entities.
    kept_area_ids: list[str] = []
    for area_id, _score in sorted(
        area_scores.items(), key=lambda item: (-item[1], item[0])
    ):
        if area_id in spoken_area_ids or any(
            ent.area_id == area_id for ent in ranked_entities
        ):
            if area_id not in kept_area_ids:
                kept_area_ids.append(area_id)
    ranked_areas = [
        area_by_id[aid] for aid in kept_area_ids if aid in area_by_id
    ][: max(1, max_areas)]

    return FilterResult(
        folded_text=fold(text),
        tokens=tokens,
        expanded_tokens=expanded,
        areas=ranked_areas,
        entities=ranked_entities,
        area_scores=area_scores,
        entity_scores={e.entity_id: entity_scores.get(e.entity_id, 0) for e in ranked_entities},
        satellite_area_id=satellite_area_id,
        is_query=is_query,
    )
