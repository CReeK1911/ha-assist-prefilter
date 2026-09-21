"""Exposed-entity catalog and the LLM context block.

Dataclasses and render() have no Home Assistant imports so unit tests can
construct a catalog by hand. Registry I/O lives in the HA helpers at the
bottom of this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .const import (
    DEFAULT_EXCLUDE_DOMAINS,
    DEFAULT_VOICE_DOMAINS,
    QUERY_INCLUDE_DOMAINS,
)
from .normalize import generated_aliases

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .filter import FilterResult


@dataclass(frozen=True)
class CatalogArea:
    """One Assist-visible area."""

    area_id: str
    name: str
    aliases: tuple[str, ...] = ()
    floor_id: str | None = None
    floor_name: str | None = None


@dataclass(frozen=True)
class CatalogEntity:
    """One Assist-exposed entity."""

    entity_id: str
    name: str
    domain: str
    aliases: tuple[str, ...] = ()
    area_id: str | None = None
    area_name: str | None = None
    area_aliases: tuple[str, ...] = ()
    floor_id: str | None = None
    floor_name: str | None = None
    state: str | None = None
    device_class: str | None = None


@dataclass
class Catalog:
    """Snapshot of areas + exposed entities."""

    areas: list[CatalogArea] = field(default_factory=list)
    entities: list[CatalogEntity] = field(default_factory=list)

    def with_states(self, states: dict[str, str | None]) -> Catalog:
        """Return a copy with entity states overlaid (cache-friendly)."""
        return Catalog(
            areas=list(self.areas),
            entities=[
                CatalogEntity(
                    entity_id=ent.entity_id,
                    name=ent.name,
                    domain=ent.domain,
                    aliases=ent.aliases,
                    area_id=ent.area_id,
                    area_name=ent.area_name,
                    area_aliases=ent.area_aliases,
                    floor_id=ent.floor_id,
                    floor_name=ent.floor_name,
                    state=states.get(ent.entity_id, ent.state),
                    device_class=ent.device_class,
                )
                for ent in self.entities
            ],
        )

    def area_by_id(self, area_id: str | None) -> CatalogArea | None:
        """Look up an area."""
        if not area_id:
            return None
        for area in self.areas:
            if area.area_id == area_id:
                return area
        return None


def _format_area_line(area: CatalogArea) -> str:
    aliases = list(area.aliases)
    alias_txt = f" aliases: {', '.join(aliases)}" if aliases else ""
    floor = f" floor={area.floor_name}" if area.floor_name else ""
    return f"- {area.name} (id={area.area_id}){alias_txt}{floor}"


def _format_entity_line(ent: CatalogEntity, include_state: bool) -> str:
    parts = [f"- {ent.entity_id} | name={ent.name}"]
    if ent.aliases:
        parts.append(f"aliases={', '.join(ent.aliases)}")
    if ent.area_name:
        parts.append(f"area={ent.area_name}")
    if include_state and ent.state is not None:
        parts.append(f"state={ent.state}")
    if ent.device_class:
        parts.append(f"class={ent.device_class}")
    return " | ".join(parts)


def _action_line(action: str | None, areas: list[CatalogArea]) -> str | None:
    if action not in {"turn_off", "turn_on"}:
        return None
    room = areas[0].name if areas else "the matched room"
    if action == "turn_off":
        verb = "turn off"
        already = "already off"
        wanted = "on"
    else:
        verb = "turn on"
        already = "already on"
        wanted = "off"
    return (
        f"Action: {verb} the lights that are {wanted} in {room}. "
        f"Call the {verb} tool for each listed light that is {wanted}. "
        "Do not ask which action to take. "
        f"If every listed light is {already}, say that."
    )


def render_context_block(
    areas: list[CatalogArea],
    entities: list[CatalogEntity],
    *,
    include_state: bool = True,
    action: str | None = None,
) -> str:
    """Tiny authoritative block injected via extra_system_prompt."""
    if not areas and not entities:
        return (
            "# Assist Prefilter context (authoritative)\n"
            "No matching devices for this utterance.\n"
            "Do not guess entity_ids. Do not search the rest of the house.\n"
            "If the user is asking to control a device, ask a short clarification.\n"
            "If the user is chatting or asking a general question, answer normally "
            "without inventing Home Assistant entity_ids.\n"
        )

    lines = [
        "# Assist Prefilter context (authoritative)",
        "Only use these targets. entity_id is the only valid id.",
        "Spoken names may contain ÅÄÖ; ids are ASCII slugs (kök → kok).",
        "",
        "Areas:",
    ]
    if areas:
        lines.extend(_format_area_line(area) for area in areas)
    else:
        lines.append("- (none)")

    lines.extend(["", "Entities:"])
    if entities:
        lines.extend(_format_entity_line(ent, include_state) for ent in entities)
    else:
        lines.append("- (none)")

    action_line = _action_line(action, areas)
    if action_line:
        lines.extend(["", action_line])

    lines.extend(
        [
            "",
            "Rules:",
            "- Map spoken Swedish names to the entity_id above. Do not invent ids.",
            "- If the command is not about these devices, say you are unsure. "
            "Do not search the rest of the house.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_from_filter(
    result: FilterResult,
    *,
    include_state: bool = True,
    fallback: Catalog | None = None,
) -> str:
    """Render hits, or a zero-hit / full-catalog fallback."""
    if result.entities or result.areas:
        return render_context_block(
            result.areas,
            result.entities,
            include_state=include_state,
            action=result.action,
        )
    if fallback is not None:
        return render_context_block(
            fallback.areas, fallback.entities, include_state=include_state
        )
    return render_context_block([], [], include_state=include_state)


def _should_expose_fallback(hass: HomeAssistant, entity_id: str) -> bool:
    """Registry-options fallback when async_should_expose is unavailable."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return False
    options = entry.options.get("conversation", {})
    if "should_expose" in options:
        return bool(options["should_expose"])
    return not entry.hidden_by and not entry.disabled_by


def infer_area_id(entity_id: str, area_ids: set[str]) -> str | None:
    """If the object id contains exactly one area slug token, use it.

    Covers scripts like filmbelysning_kok that have no registry area.
    """
    object_id = entity_id.split(".", 1)[-1]
    tokens = set(object_id.split("_"))
    matches = [area_id for area_id in area_ids if area_id in tokens]
    if not matches:
        return None
    return max(matches, key=len)


def async_should_expose_compat(hass: HomeAssistant, entity_id: str) -> bool:
    """Same expose flag Core conversation uses, with a small version fork."""
    try:
        from homeassistant.components.homeassistant.exposed_entities import (
            async_should_expose,
        )
        from homeassistant.components.conversation import DOMAIN as CONV_DOMAIN

        return async_should_expose(hass, CONV_DOMAIN, entity_id)
    except (ImportError, AttributeError):
        return _should_expose_fallback(hass, entity_id)


def _entity_device_class(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None
    value = state.attributes.get("device_class")
    return str(value) if value else None


def build_catalog(
    hass: HomeAssistant,
    *,
    exclude_domains: set[str] | frozenset[str] | None = None,
    include_query_domains: bool = False,
    include_state: bool = True,
) -> Catalog:
    """Walk registries and keep Assist-exposed voice-control entities."""
    from homeassistant.helpers import (
        area_registry as ar,
        device_registry as dr,
        entity_registry as er,
        floor_registry as fr,
    )

    excluded = set(exclude_domains or DEFAULT_EXCLUDE_DOMAINS)
    if include_query_domains:
        excluded -= set(QUERY_INCLUDE_DOMAINS)

    area_reg = ar.async_get(hass)
    floor_reg = fr.async_get(hass)
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    areas: list[CatalogArea] = []
    # HA 2024.4+ has async_list_areas(); older snapshots expose .areas.
    area_iter = (
        area_reg.async_list_areas()
        if hasattr(area_reg, "async_list_areas")
        else area_reg.areas.values()
    )
    for area in area_iter:
        floor_name = None
        if area.floor_id:
            get_floor = getattr(floor_reg, "async_get_floor", None) or getattr(
                floor_reg, "async_get", None
            )
            floor = get_floor(area.floor_id) if get_floor else None
            floor_name = floor.name if floor else None
        area_name = (area.name or "").strip()
        aliases = tuple(sorted({*(area.aliases or ()), *generated_aliases(area_name)}))
        # Python AreaEntry uses .id; websocket payloads use area_id.
        area_id = getattr(area, "id", None) or getattr(area, "area_id", None)
        if not area_id:
            continue
        areas.append(
            CatalogArea(
                area_id=str(area_id),
                name=area_name,
                aliases=aliases,
                floor_id=area.floor_id,
                floor_name=floor_name,
            )
        )
    area_index = {a.area_id: a for a in areas}

    entities: list[CatalogEntity] = []
    for state in hass.states.async_all():
        entity_id = state.entity_id
        domain = entity_id.split(".", 1)[0]
        if domain in excluded:
            continue
        if domain not in DEFAULT_VOICE_DOMAINS and domain not in QUERY_INCLUDE_DOMAINS:
            continue
        if domain in QUERY_INCLUDE_DOMAINS and not include_query_domains:
            continue
        if not async_should_expose_compat(hass, entity_id):
            continue

        entry = ent_reg.async_get(entity_id)
        name = state.name
        aliases_set: set[str] = set()
        area_id = None
        if entry:
            if entry.hidden_by or entry.disabled_by:
                continue
            if entry.name:
                name = entry.name
            aliases_set.update(entry.aliases or ())
            area_id = entry.area_id
            if area_id is None and entry.device_id:
                device = dev_reg.async_get(entry.device_id)
                if device:
                    area_id = device.area_id
        if not area_id:
            area_id = infer_area_id(entity_id, set(area_index))
        aliases_set.update(generated_aliases(name))
        aliases_set.discard(name)

        area = area_index.get(area_id) if area_id else None
        entities.append(
            CatalogEntity(
                entity_id=entity_id,
                name=name,
                domain=domain,
                aliases=tuple(sorted(aliases_set)),
                area_id=area.area_id if area else area_id,
                area_name=area.name if area else None,
                area_aliases=area.aliases if area else (),
                floor_id=area.floor_id if area else None,
                floor_name=area.floor_name if area else None,
                state=state.state if include_state else None,
                device_class=_entity_device_class(hass, entity_id),
            )
        )

    entities.sort(key=lambda e: e.entity_id)
    areas.sort(key=lambda a: a.area_id)
    return Catalog(areas=areas, entities=entities)


def satellite_area_id(
    hass: HomeAssistant,
    device_id: str | None,
    satellite_id: str | None,
) -> str | None:
    """Area of the voice satellite / pipeline device, if any."""
    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    for ident in (device_id, satellite_id):
        if not ident:
            continue
        device = registry.async_get(ident)
        if device is not None and device.area_id:
            return device.area_id
    return None


class CatalogCache:
    """Rebuild on registry updates, not on every utterance."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._catalog: Catalog | None = None
        self._unsubs: list[Any] = []

    def async_setup(self) -> None:
        """Listen for registry changes."""
        from homeassistant.helpers.area_registry import EVENT_AREA_REGISTRY_UPDATED
        from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED

        self._unsubs.append(
            self.hass.bus.async_listen(EVENT_ENTITY_REGISTRY_UPDATED, self._invalidate)
        )
        self._unsubs.append(
            self.hass.bus.async_listen(EVENT_AREA_REGISTRY_UPDATED, self._invalidate)
        )
        try:
            from homeassistant.helpers.floor_registry import (
                EVENT_FLOOR_REGISTRY_UPDATED,
            )

            self._unsubs.append(
                self.hass.bus.async_listen(
                    EVENT_FLOOR_REGISTRY_UPDATED, self._invalidate
                )
            )
        except ImportError:
            pass

    def async_unload(self) -> None:
        """Drop listeners."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._catalog = None

    def _invalidate(self, *_args: Any, **_kwargs: Any) -> None:
        self._catalog = None

    def invalidate(self) -> None:
        """Force rebuild on next get()."""
        self._catalog = None

    def get(
        self,
        *,
        exclude_domains: set[str] | frozenset[str] | None = None,
        include_query_domains: bool = False,
        include_state: bool = True,
    ) -> Catalog:
        """Return a (possibly cached) catalog, overlaying live states.

        The cache stores every exposed voice + query-domain entity. Domain
        excludes are applied here so options changes do not require a rebuild.
        """
        if self._catalog is None:
            # Cache a superset; filter domains per call.
            self._catalog = build_catalog(
                self.hass,
                exclude_domains=set(),
                include_query_domains=True,
                include_state=False,
            )
        excluded = set(exclude_domains or DEFAULT_EXCLUDE_DOMAINS)
        entities: list[CatalogEntity] = []
        for ent in self._catalog.entities:
            if ent.domain in QUERY_INCLUDE_DOMAINS and not include_query_domains:
                continue
            if ent.domain in excluded and not (
                include_query_domains and ent.domain in QUERY_INCLUDE_DOMAINS
            ):
                continue
            entities.append(ent)
        catalog = Catalog(areas=list(self._catalog.areas), entities=entities)
        if include_state:
            states = {
                ent.entity_id: (
                    st.state if (st := self.hass.states.get(ent.entity_id)) else None
                )
                for ent in catalog.entities
            }
            return catalog.with_states(states)
        return catalog
