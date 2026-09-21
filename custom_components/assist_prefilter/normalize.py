"""Swedish-aware fold and tokenization for Assist matching.

Used on both the utterance and every catalog string. No HA imports.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

# Generated aliases, not a full morphology. Compound handling uses these as
# suffixes (taklampan → taklampa via lampan → lampa).
SV_DEFINITE: dict[str, tuple[str, ...]] = {
    "kök": ("kök", "köket"),
    "hall": ("hall", "hallen"),
    "sovrum": ("sovrum", "sovrummet"),
    "badrum": ("badrum", "badrummet"),
    "vardagsrum": ("vardagsrum", "vardagsrummet"),
    "toalett": ("toalett", "toaletten"),
    "kontor": ("kontor", "kontoret"),
    "garage": ("garage", "garaget"),
    "källare": ("källare", "källaren"),
    "vind": ("vind", "vinden"),
    "entré": ("entré", "entrén"),
    "lampa": ("lampa", "lampan", "lampor", "lamporna"),
    "ljus": ("ljus", "ljuset", "ljusen"),
    "belysning": ("belysning", "belysningen", "belysningar", "belysningarna"),
    "taklampa": ("taklampa", "taklampan", "taklampor", "taklamporna"),
    "takbelysning": (
        "takbelysning",
        "takbelysningen",
        "takbelysningar",
        "takbelysningarna",
    ),
    "golvlampa": ("golvlampa", "golvlampan"),
    "fönsterlampa": ("fönsterlampa", "fönsterlampan", "fönsterlampor"),
    "bänklampa": ("bänklampa", "bänklampan"),
    "bänk": ("bänk", "bänken", "bänkar", "bänkarna"),
    "köksbänk": ("köksbänk", "köksbänken"),
    "underskåp": ("underskåp", "underskåpet", "underskåps"),
    "skåp": ("skåp", "skåpet", "skåps"),
    "matplats": ("matplats", "matplatsen"),
    "matsal": ("matsal", "matsalen"),
    "julgran": ("julgran", "julgranen"),
    "gran": ("gran", "granen"),
    "gardin": ("gardin", "gardinen", "gardiner", "gardinerna"),
    "rullgardin": ("rullgardin", "rullgardinen", "rullgardiner"),
    "dörr": ("dörr", "dörren", "dörrar", "dörrarna"),
    "fönster": ("fönster", "fönstret", "fönstren"),
    "fläkt": ("fläkt", "fläkten", "fläktar", "fläktarna"),
    "element": ("element", "elementet", "elementen"),
    "termostat": ("termostat", "termostaten"),
    "brytare": ("brytare", "brytaren", "brytarna"),
    "uttag": ("uttag", "uttaget", "uttagen"),
    "lås": ("lås", "låset", "låsen"),
    "scen": ("scen", "scenen", "scener", "scenerna"),
    "tv": ("tv", "teve", "teven"),
    "högtalare": ("högtalare", "högtalaren", "högtalarna"),
    "dammsugare": ("dammsugare", "dammsugaren"),
    "värme": ("värme", "värmen"),
    "spegel": ("spegel", "spegeln"),
    "trappa": ("trappa", "trappan"),
}

# Stems that should match each other after folding (taklampa ↔ takbelysning).
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("taklampa", "takbelysning", "takljus"),
    ("lampa", "belysning"),
    ("fönsterlampa", "fonsterlampa"),
    ("julgran", "gran"),
    ("underskåp", "underskåpsbelysning", "underskapsbelysning"),
    ("bänk", "bänklampa", "köksbänk", "koksbank"),
    ("matplats", "matsal"),
)

_TOKEN_SPLIT = re.compile(r"[^\w]+", re.UNICODE)

# Precomposed leftovers that NFKD may not fully fold the way we want.
_CHAR_MAP = str.maketrans(
    {
        "å": "a",
        "ä": "a",
        "ö": "o",
        "ø": "o",
        "æ": "ae",
        "é": "e",
        "è": "e",
        "ê": "e",
        "ë": "e",
        "á": "a",
        "à": "a",
        "â": "a",
        "ü": "u",
        "ú": "u",
        "ù": "u",
        "í": "i",
        "ì": "i",
        "ï": "i",
        "ñ": "n",
        "ç": "c",
    }
)


def _strip_combining(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if unicodedata.category(ch) != "Mn"
    )


@lru_cache(maxsize=4096)
def fold(text: str) -> str:
    """Lowercase, map ÅÄÖ, strip combining marks. Keep letters and digits."""
    lowered = text.casefold()
    mapped = lowered.translate(_CHAR_MAP)
    stripped = _strip_combining(mapped)
    return stripped


def tokenize(text: str) -> list[str]:
    """Split on non-letters into lowercase original tokens (pre-fold)."""
    parts = _TOKEN_SPLIT.split(text.strip())
    return [p for p in parts if p]


def folded_tokens(text: str) -> list[str]:
    """Tokenize then fold each token."""
    return [fold(tok) for tok in tokenize(text) if fold(tok)]


def entity_id_tokens(entity_id: str) -> list[str]:
    """Tokens from the object id tail: light.kok_taklampa → kok, taklampa."""
    _, _, object_id = entity_id.partition(".")
    if not object_id:
        object_id = entity_id
    return folded_tokens(object_id.replace("_", " "))


def _definite_index() -> tuple[dict[str, str], list[tuple[str, str]]]:
    """folded form → stem, and (folded form, folded stem) longest-first."""
    form_to_stem: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for stem, forms in SV_DEFINITE.items():
        stem_f = fold(stem)
        for form in forms:
            form_f = fold(form)
            form_to_stem[form_f] = stem_f
            form_to_stem.setdefault(stem_f, stem_f)
            pairs.append((form_f, stem_f))
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return form_to_stem, pairs


_FORM_TO_STEM, _FORM_PAIRS = _definite_index()

_MIN_SUBSTRING_STEM = 4


def _synonym_index() -> dict[str, frozenset[str]]:
    idx: dict[str, set[str]] = {}
    for group in SYNONYM_GROUPS:
        folded = {fold(item) for item in group}
        for item in folded:
            idx.setdefault(item, set()).update(folded)
            stem = _FORM_TO_STEM.get(item)
            if stem:
                idx[item].add(stem)
                idx.setdefault(stem, set()).update(folded)
    return {key: frozenset(value) for key, value in idx.items()}


_SYNONYMS = _synonym_index()


def _with_synonyms(tokens: set[str]) -> set[str]:
    extra: set[str] = set()
    for item in tokens:
        extra.update(_SYNONYMS.get(item, ()))
        stem = _FORM_TO_STEM.get(item)
        if stem:
            extra.update(_SYNONYMS.get(stem, ()))
    tokens.update(extra)
    return tokens


@lru_cache(maxsize=8192)
def expand_token(token: str) -> frozenset[str]:
    """Folded token plus stems / definite variants / compounds / synonyms."""
    folded = fold(token)
    if not folded:
        return frozenset()
    out: set[str] = {folded}
    if folded in _FORM_TO_STEM:
        stem = _FORM_TO_STEM[folded]
        out.add(stem)
        for form, form_stem in _FORM_PAIRS:
            if form_stem == stem:
                out.add(form)
    for form_f, stem_f in _FORM_PAIRS:
        if folded.endswith(form_f) and len(folded) > len(form_f):
            prefix = folded[: -len(form_f)]
            out.add(prefix + stem_f)
            out.add(prefix + form_f)
            out.add(stem_f)
        elif len(form_f) >= _MIN_SUBSTRING_STEM and form_f in folded:
            out.add(form_f)
            out.add(stem_f)
    return frozenset(_with_synonyms(out))


def expand_tokens(tokens: list[str]) -> set[str]:
    """Union of expand_token for each token."""
    out: set[str] = set()
    for token in tokens:
        out.update(expand_token(token))
    return out


def generated_aliases(name: str) -> list[str]:
    """Extra aliases from SV_DEFINITE for a display name."""
    extras: list[str] = []
    seen: set[str] = set()
    for token in tokenize(name):
        folded = fold(token)
        if folded in _FORM_TO_STEM:
            stem = _FORM_TO_STEM[folded]
            for form, form_stem in _FORM_PAIRS:
                if form_stem == stem and form not in seen:
                    seen.add(form)
                    extras.append(form)
    return extras


def fold_match(a: str, b: str) -> bool:
    """True if folded strings are equal or share an expanded token."""
    if fold(a) == fold(b):
        return True
    a_exp = expand_tokens(tokenize(a))
    b_exp = expand_tokens(tokenize(b))
    return bool(a_exp & b_exp)
