# Assist Prefilter

A HACS **conversation agent** that sits in the Assist pipeline **after STT**. It reduces the house to the spoken area and entities (with Swedish ÅÄÖ ↔ ASCII slug matching) and hands the **original sentence** plus that short list to your existing LLM agent (Ollama, OpenAI, Gemini, …).

This integration is **not an LLM**. It does not do STT or TTS. It only shrinks context.

```
wake → STT → Assist Prefilter
                1. optional local Assist intents
                2. resolve spoken names (kök / köket / kok)
                3. filter areas + entities
                4. call your LLM agent with extra_system_prompt = filtered catalog
           → TTS (speech comes from the downstream agent)
```

## Requirements

- Home Assistant **2025.4** or later (ConversationEntity + `extra_system_prompt`)
- An existing LLM conversation agent already set up
- HACS (recommended) or a manual copy into `custom_components/`

## Install via HACS

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add this GitHub repo as category **Integration**
3. Install **Assist Prefilter** and **restart Home Assistant**
4. Settings → Devices & services → Add integration → **Assist Prefilter**
5. Pick a **name** and the **downstream conversation agent** (Ollama / OpenAI / Gemini / …)
6. On that downstream agent, set the Home Assistant control API to **Assist Prefilter** instead of **Assist**. Do not select both. Assist still pastes the whole house into the prompt; Assist Prefilter replaces that list with this request's matches.

Manual install: copy `custom_components/assist_prefilter/` into `/config/custom_components/` and restart.

## Wire it into Assist

This is the step that is easy to miss:

1. Settings → **Voice assistants** → your pipeline (Voice PE, Wyoming satellite, phone, …)
2. Set **Conversation agent** to **Assist Prefilter**, **not** to the LLM directly
3. Leave **Speech-to-text** and **Text-to-speech** as they were

You can add more than one Prefilter entry (for example “prefilter → Ollama” and “prefilter → cloud”) and point different pipelines at different entries.

## Voice aliases (Swedish)

STT often drops diacritics (`kök` → `kok`). Entity ids are ASCII slugs (`light.kok_taklampa`) while friendly names still have ÅÄÖ. Add **both** forms as Assist aliases:

| Spoken | Alias / name | entity_id |
| --- | --- | --- |
| kök, köket, kok, koket | Kök | area `kok` |
| taklampan i köket | Köks taklampa, kökslampa | `light.kok_taklampa` |
| hallen | Hall | area `hall` |

The prefilter folds ÅÄÖ (`å/ä→a`, `ö→o`), expands a small table of Swedish definite forms (`kök→köket`, `lampa→lampan`), and matches those against names, aliases, area names, and `entity_id` tail tokens.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| Prefer local Home Assistant intents | on | Fast path via `conversation.home_assistant`; LLM only on miss |
| Max entities / areas | 24 / 8 | Hard cap on the context block |
| Include entity state | on | Adds `state=on` etc. |
| Fall back to full catalog | **off** | Off: zero hits → “do not guess ids”. On: dump the house (reintroduces latency) |
| Exclude domains | sensors, updates, … | Sensors come back in when the utterance looks like a query (`hur`, `vad är`, `how`, `what`) |
| Needle URL / enabled | off | Optional second-stage filter |

## Debug without speaking

Developer tools → Actions → `assist_prefilter.debug_filter`

```yaml
action: assist_prefilter.debug_filter
data:
  text: släck taklampan i köket
```

The response contains folded text, tokens, matched areas, scored entities, and the context block that would be sent.

The integration also fires `assist_prefilter_filtered` with counts and `elapsed_ms` (no full transcript at INFO).

## Optional Needle 3 sidecar

Needle is **not** required. When enabled, Assist Prefilter POSTs the already-filtered candidates to a local HTTP sidecar and keeps the heuristic list on timeout/error (400 ms). Do not vendor the engine into Home Assistant OS.

Example:

```
./needle --model needle3.cact --tools /config/needle-ha-tools.json --serve
```

Default URL: `http://127.0.0.1:8099/complete`

JSON the sidecar should understand (any of these shapes):

```json
{
  "entity_ids": ["light.kok_taklampa"],
  "areas": ["kok"],
  "intent": "control"
}
```

A v2 add-on wrapping Needle is out of scope for this release.

## Filtered LLM API (required for speed)

The pipeline conversation agent only adds a short note. Home Assistant's **Assist** API still inserts a "Static Context" YAML of every exposed entity, and that is what makes a local model slow.

This integration registers an LLM API named **Assist Prefilter**. It keeps Assist's control tools (`HassTurnOn` and the rest) and `GetLiveContext`, but the static list and live-context tool are limited to the entities the prefilter just matched.

On each downstream conversation agent (the LLM, not the pipeline):

1. Open its settings.
2. Under control / LLM API, select **Assist Prefilter**.
3. Remove **Assist** if it is also selected. Leaving both in sends the full house again.

If you talk to that LLM agent directly, without going through the prefilter pipeline, the API falls back to the full Assist list.

## Limitation

Intent tools can still act on any exposed entity if the model invents a name. The prompt no longer lists those entities. A custom tool filter that rejects calls outside the hit list is not in this version. Needle is still optional and does not shrink Static Context.

## Manual checks after install

1. Pipeline Prefilter → your LLM. “släck taklampan i köket” — event shows `light.kok_*` and area kök; the light turns off without a long think.
2. “släck lampan” on a satellite whose device area is köket → kitchen lights, not the whole house.
3. “vad är klockan” / chit-chat → low `entity_count` on the event.
4. Disable the downstream agent → short spoken error, no traceback.
5. Prefer local intents: a built-in sentence is handled by Home Assistant; event `local_intent: true`.

## Development

```bash
pytest
```

Unit tests cover normalize / filter / context render and do not need a Home Assistant instance.
