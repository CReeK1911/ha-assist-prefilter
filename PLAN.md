# Plan: HACS Conversation Prefilter Agent for Home Assistant

Build a HACS custom integration that **appears as a Conversation Agent** so it can be selected in an Assist voice pipeline. After STT, it **narrows entities and areas** relevant to the utterance (including Swedish ÅÄÖ ↔ slug `aao` mapping), then **forwards the same user text plus a tight context block** to an existing LLM conversation agent. That restores the original Assist path (Ollama / OpenAI / Gemini / etc.) without dumping the whole house into the prompt.

Needle 3 is an **optional** second-stage filter, not required for v1.

Do not invent HA APIs. Use current Core conversation APIs (`ConversationEntity._async_handle_message`, `conversation.async_converse`, `extra_system_prompt`, entity/area/floor registries, Assist expose flags).

---

## Goal

Voice pipeline today:

```
wake → STT → [LLM conversation agent with ALL exposed entities] → TTS
```

Target:

```
wake → STT → [this agent]
                1. try local Assist intents (optional)
                2. resolve spoken names (kök / köket / kok)
                3. filter areas + entities to a short list
                4. call configured downstream LLM agent
                   with extra_system_prompt = filtered catalog
           → TTS (speech comes from downstream agent)
```

This integration must:

- Register a real `conversation` platform entity (`conversation.<name>`).
- Show up under Settings → Voice assistants → Conversation agent.
- Be selectable on an Assist pipeline (Voice PE / Wyoming satellites included).
- Not implement STT or TTS.
- Not replace the user’s LLM. It only shrinks context.

---

## Non-goals (v1)

- Shipping Needle weights inside HA Core process as a hard dependency.
- Generating spoken chat itself (no jokes, no weather essays).
- Rewriting entity_ids in the registry.
- A custom Lovelace card.
- Fine-tuning UI for Needle (v2).

---

## Repository layout (HACS-valid)

```
ha-assist-prefilter/
├── README.md
├── LICENSE
├── hacs.json
├── PLAN.md
├── .github/workflows/validate.yml
└── custom_components/assist_prefilter/
    ├── __init__.py
    ├── manifest.json
    ├── const.py
    ├── config_flow.py
    ├── conversation.py
    ├── filter.py
    ├── normalize.py
    ├── catalog.py
    ├── needle_client.py
    ├── strings.json
    ├── translations/en.json
    ├── translations/sv.json
    └── services.yaml
```

Domain: `assist_prefilter`  
Name: `Assist Prefilter`  
HACS category: Integration

`hacs.json`:

```json
{
  "name": "Assist Prefilter",
  "homeassistant": "2025.4.0",
  "render_readme": true
}
```

`manifest.json`:

```json
{
  "domain": "assist_prefilter",
  "name": "Assist Prefilter",
  "codeowners": ["@REPLACE_ME"],
  "config_flow": true,
  "dependencies": ["conversation", "assist_pipeline"],
  "after_dependencies": ["openai_conversation", "google_generative_ai_conversation", "ollama"],
  "documentation": "https://github.com/REPLACE_ME/ha-assist-prefilter",
  "issue_tracker": "https://github.com/REPLACE_ME/ha-assist-prefilter/issues",
  "iot_class": "local_push",
  "integration_type": "service",
  "requirements": [],
  "version": "0.1.0"
}
```

Keep `requirements` empty in v1. Needle HTTP client uses `aiohttp` already in HA. If Needle Python package is added later, put it behind an option, not a hard pip install that breaks HA OS.

Minimum HA: **2025.4** (ConversationEntity + `_async_handle_message` + `extra_system_prompt`). Prefer testing on latest stable.

---

## Architecture

### 1. Conversation entity

`conversation.py` implements:

```python
class AssistPrefilterEntity(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
):
```

Required:

- `supported_languages` → `"*"` (language is the downstream agent’s problem; filtering is language-agnostic plus Swedish fold).
- `_attr_supported_features = ConversationEntityFeature.CONTROL` so pipelines treat it as a controlling agent.
- `_async_handle_message(user_input, chat_log) -> ConversationResult`

Pipeline hook: user selects **this** entity as the pipeline conversation agent. Assist calls `internal_async_process` → `async_process` → `_async_handle_message` with the STT transcript in `user_input.text`.

### 2. Handle-message flow

```
_async_handle_message(user_input, chat_log)
  ├─ if options.prefer_local_intents:
  │     result = await hass.services / conversation.async_converse(
  │                  agent_id="conversation.home_assistant", ...)
  │     if result is not no_intent_match / no_valid_targets:
  │         return that result          # fast path, no LLM
  ├─ catalog = Catalog.from_hass(hass)  # exposed only
  ├─ hits = filter_catalog(user_input.text, catalog, device_id)
  ├─ if options.use_needle and needle configured:
  │     hits = await needle_refine(text, hits)
  ├─ prompt = render_context_block(hits)
  ├─ return await conversation.async_converse(
  │       hass,
  │       text=user_input.text,          # original utterance, unchanged
  │       conversation_id=user_input.conversation_id,
  │       context=user_input.context,
  │       language=user_input.language,
  │       agent_id=options.downstream_agent_id,
  │       device_id=user_input.device_id,
  │       satellite_id=user_input.satellite_id,
  │       extra_system_prompt=prompt,
  │   )
```

Critical: **do not rewrite the user sentence**. The LLM still sees “släck lampan i köket”. Only the *house catalog* shrinks.

Delegation API (Core):

```python
from homeassistant.components.conversation import async_converse, async_get_agent
```

`extra_system_prompt` is appended by official LLM agents when they call `chat_log.async_provide_llm_data(...)`. That is the supported injection point. Do not monkeypatch another integration’s prompt option.

Guardrails:

- Never set `downstream_agent_id` to this entity (recursion). Validate in config flow and again at runtime.
- If downstream agent is missing/unavailable → `IntentResponse` error speech, short Swedish/English string.
- Cap filter output: default `max_entities=24`, `max_areas=8`.
- If filter returns **zero** hits: still forward, but with a prompt that says “no matching devices; do not guess entity_ids; ask a short clarification”. Do not dump the full house as fallback (that reintroduces the original latency). Optional config `fallback_to_full_catalog: false` by default.

### 3. Catalog (what the LLM is allowed to see)

Build from registries, **exposed-to-Assist only** (same set HA already uses for LLM tools).

Per entity:

- `entity_id` (slug, e.g. `light.kok_taklampa`)
- `name` (friendly / registry name, may contain ÅÄÖ)
- `aliases` (voice aliases)
- `area_id`, `area_name`, `area_aliases`
- `floor_id`, `floor_name` if present
- `domain`
- `state` (optional, short; skip attributes)
- `device_class` if useful (light, cover, climate)

Per area / floor: id, name, aliases.

Satellite hint: if `user_input.device_id` / `satellite_id` maps to a device in an area, boost that area’s entities (same idea as Assist area inference).

### 4. Normalizer (Swedish slug problem)

`normalize.py` is the whole point of the middle layer.

Implement a fold used on **both** the utterance and every catalog string:

1. Unicode NFKD, lowercase.
2. Map: `å→a`, `ä→a`, `ö→o`, `é→e`, … plus strip combining marks.
3. Keep a second key with the original letters.
4. Tokenize on non-letters.

Match if any of these hit:

- exact alias / name
- folded alias / name (`kök` matches `kok`)
- area name in utterance (`i köket`, `köket`, `koket` if STT dropped diacritics)
- entity_id tail tokens (`kok_taklampa` ↔ tokens `kok`, `taklampa`)

Also index **definite Swedish forms** as generated aliases, not hardcoded linguistics beyond a small table:

```
kök → kök, köket
hall → hall, hallen
lampa → lampa, lampan, lampor, lamporna
```

Keep this table data-driven in `normalize.py` (`SV_DEFINITE`). Do not pull a full NLP library.

### 5. Filter algorithm (v1, no Needle required)

`filter.py`:

1. Fold + tokenize utterance.
2. Score every area: name/alias/fold overlap. Keep areas above threshold or top-N.
3. Score every exposed entity:
   - +3 name/alias token overlap
   - +2 area match (spoken area or satellite area)
   - +1 domain cue words (`lampa/light`, `värme/climate`, `gardin/cover`, `scen/script`…)
   - +1 satellite-area boost
4. Always include matched areas’ “primary” devices if an area matched but no entity did (all lights in köket).
5. Hard cap. Stable sort by score, then entity_id.

Domain cue lexicon in `const.py` for sv + en at minimum.

This is deterministic and fast (<5 ms). It is the default path.

### 6. Context block sent to the LLM

Keep it tiny and explicit so the model stops “reasoning” about slugs:

```
# Assist Prefilter context (authoritative)
Only use these targets. entity_id is the only valid id.
Spoken names may contain ÅÄÖ; ids are ASCII slugs (kök → kok).

Areas:
- kök (id=kok) aliases: köket, kok, koket

Entities:
- light.kok_taklampa | name=Köks taklampa | aliases=kökslampa, taklampan i köket | area=kök | state=on
- light.kok_bänk | name=Bänkbelysning | area=kök | state=off

Rules:
- Map spoken Swedish names to the entity_id above. Do not invent ids.
- If the command is not about these devices, say you are unsure. Do not search the rest of the house.
```

Inject via `extra_system_prompt` only. Do not strip the downstream agent’s own prompt or tools. The official Assist LLM API still exposes tools for the whole house; the prompt rule is the v1 mitigation. v1.1 can add a custom `llm.API` that wraps Assist tools and drops non-hit entity_ids (see Stretch).

### 7. Optional Needle 3

`needle_client.py`:

- Config: `needle_url` (default `http://127.0.0.1:8099/complete`), timeout 400 ms.
- Input: utterance + compact tool schema of **already filtered** candidates (or top 40 before cap).
- Tools like:

```
select_targets(areas: list[str], entity_ids: list[str], intent: "control"|"query"|"other")
```

- If Needle returns a subset, use it. On timeout/error, keep heuristic hits.
- Never block the voice path on Needle.

Do not vendor `cactus-needle` into the custom component for v1. Document a sidecar:

```
./needle --model needle3.cact --tools /config/needle-ha-tools.json --serve
```

v2 can add an HA add-on. Out of scope for first build.

---

## Config flow

UI setup (config_flow + options flow):

1. Name (default `Assist Prefilter`)
2. **Downstream conversation agent** — dropdown of `conversation.*` entities excluding our own. Required.
3. Prefer local Home Assistant intents first (bool, default true)
4. Max entities (int, 8–80, default 24)
5. Max areas (int, 1–20, default 8)
6. Include entity state in context (bool, default true)
7. Fallback to full catalog if no hits (bool, default false)
8. Extra domains to always exclude (multi-select: `sensor`, `binary_sensor`, `update`, … default exclude noisy sensors unless utterance looks like a query)
9. Needle URL (optional string)
10. Needle enabled (bool, default false)

Store on `ConfigEntry.options`. One config entry = one conversation entity (user may add multiple entries: “prefilter → Ollama”, “prefilter → cloud”).

`strings.json` / `translations/sv.json` for all form labels.

Agent picker implementation: iterate `hass.states.async_entity_ids("conversation")` in the flow. Flows that run before entities exist should allow a string entity_id and validate on submit.

---

## Integration setup

`__init__.py`:

- `async_setup_entry` → `hass.config_entries.async_forward_entry_setups(entry, ["conversation"])`
- Reload on options update
- `async_unload_entry`

`conversation.py` `async_setup_entry`: add `AssistPrefilterEntity(hass, entry)`.

Entity unique_id = entry.entry_id. Name from options.

---

## Services / debug (needed for development)

`assist_prefilter.debug_filter`

Fields: `text`, optional `agent_entity_id`.

Response (as a persistent notification or `response` in service): tokens, folded text, matched areas, scored entities, context block that would be sent.

This is how you verify `kök` → `light.kok_taklampa` without speaking.

Also fire an event `assist_prefilter_filtered` with counts + elapsed_ms for traces.

---

## Tests (required in the build)

No full HA instance required for unit tests:

- `tests/test_normalize.py` — `kök`/`köket`/`Koket`/`kok` all fold-match; `sovrum` does not match `kök`.
- `tests/test_filter.py` — fake catalog with `light.kok_taklampa` name “Köks taklampa” alias “kökslampa”; utterance “släck taklampan i köket” returns that entity and area `kök`, not `light.vardagsrum_tv`.
- `tests/test_filter.py` — satellite in kitchen boosts kitchen lights when utterance is just “släck lampan”.
- `tests/test_filter.py` — cap respected.
- `tests/test_catalog_render.py` — context block contains entity_id and spoken name, not a 200-entity dump.

If the builder can run `pytest` only on those modules, good. Do not require `hassfest` in the first commit, but include a GitHub workflow that runs `hacs/action` validate when possible.

---

## README (write it as part of the build)

Must include:

- What it is (prefilter conversation agent, not an LLM).
- Install via HACS custom repository.
- Create the integration, pick downstream agent (Ollama/OpenAI/Gemini).
- Set the **Assist pipeline conversation agent to Assist Prefilter**, not to the LLM directly.
- Keep STT/TTS as they were.
- Add voice aliases with real ÅÄÖ **and** slug variants if STT drops diacritics.
- Example Swedish mapping table.
- Optional Needle sidecar.
- Debug service.
- Limitation: downstream LLM Assist tools may still theoretically see all entities; the prompt forbids using them. Stretch: custom LLM API.

---

## Implementation order for Grok Build

Execute in this order. Do not skip ahead to Needle.

### Step 1 — Scaffold
Create repo files listed above. Empty but valid integration that loads and registers a conversation entity returning a fixed “prefilter not configured” speech if no downstream agent.

### Step 2 — Config flow
Name + downstream agent + numeric caps. Options flow for the rest.

### Step 3 — Catalog + normalize + filter
Pure Python, fully unit-tested. No HA I/O in normalize/filter.

### Step 4 — Catalog builder
`catalog.py` reads `entity_registry`, `area_registry`, `floor_registry`, `er.async_get`, expose check via `async_should_expose` / `homeassistant.components.homeassistant.exposed_entities` (use the same helper Core conversation uses; if import path differs on target HA version, wrap in a small compatibility function and fall back to “entity has `conversation` expose in registry options”).

Only include domains the user would voice-control by default:

```
light, switch, cover, fan, climate, media_player, lock, scene, script,
input_boolean, vacuum, water_heater, humidifier, remote, alarm_control_panel
```

Sensors included only when utterance matches query cues (`hur`, `vad är`, `temperatur`, `status`, `how`, `what`).

### Step 5 — Delegation
Wire `_async_handle_message` to local intent then `async_converse`. Copy `device_id`, `satellite_id`, `conversation_id`, `language`, `context`.

Local intent: call `async_converse(..., agent_id="conversation.home_assistant")`. Treat `response.response_type == "error"` with `no_intent_match` / `no_valid_targets` as miss. Any `action_done` / `query_answer` is a hit — return immediately.

### Step 6 — Debug service + event

### Step 7 — Swedish translations and README

### Step 8 — Optional Needle client behind flag

### Step 9 — Sanity checklist (builder should self-review)
- Domain folder name matches manifest domain.
- `version` in manifest.
- No blocking I/O in the event loop (Needle/HTTP via `aiohttp` + timeout).
- Filter work is CPU-light; if catalog rebuild is heavy, cache on entity/area registry updates (`async_track` listeners) not per-utterance full walk if possible.
- Recursion guard.
- Does not log full transcripts at INFO (privacy). Debug logger only.

---

## Stretch (only if v1 works)

1. Custom `llm.API` registered by this integration that clones Assist tools but intercepts tool calls / tool list to the filtered entity set. Then the downstream agent can be configured to use **that** API instead of default Assist API. This is the correct long-term fix; `extra_system_prompt` is the compatible short-term fix.
2. Persist last filter per `conversation_id` so “den där lampan” in turn 2 stays in the same area.
3. HA add-on wrapping Needle 3 engine + `.cact`.
4. Fine-tune note in README: generate jsonl from the user’s catalog (aliases × actions) for Needle LoRA.

---

## Acceptance tests (manual, after install)

1. Pipeline uses Prefilter → Ollama (or OpenAI). Voice or Assist debug: “släck taklampan i köket”.
   - Event shows hit `light.kok_*` and area kök.
   - Downstream LLM turns off the right light without a long think.
2. “släck lampan” on a satellite whose device area is köket → kitchen lights, not the whole house.
3. “vad är klockan” / chit-chat → zero or few entity hits, LLM answers without a 200-entity prompt (check event `entity_count`).
4. Disable downstream agent → spoken error, no traceback.
5. Prefer local intents: “turn off kitchen lights” / built-in Swedish sentence if installed → handled by `conversation.home_assistant`, event `local_intent: true`.

---

## Code style constraints for the builder

- Python 3.12+, typed, ruff-friendly.
- No wildcard imports.
- All user-facing strings through translations.
- Comments only where HA version forks are handled.
- Do not embed copyrighted HA Core files; import public helpers.

---

## One-sentence summary for the PR

A HACS conversation agent that sits in the Assist pipeline after STT, reduces the house to the spoken area/entities (with Swedish ÅÄÖ↔slug matching), and hands the original sentence plus that short list to the user’s existing LLM agent.
