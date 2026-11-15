# JINXAI — brand system (galactic)

The master brand is **JINXAI**. Every customer-facing surface speaks in the galactic
register; none of them names the models, files or nodes underneath.

## The naming scheme

| Surface | Name | Notes |
|---|---|---|
| Master | **JINXAI** | caps, one word, no space, no hyphen |
| Public app | **JINXAI ORBITAL** | the SFW portrait app and its listing title |
| Key-gated platform | **ORBITAL CORE** | API access, never a public listing |
| Batch product | **ORBITAL CONSTELLATION** | prompt-list renders with pollable job ids |
| Tiers | **COMET · PULSAR · QUASAR** | entry, working, volume |
| Unit, still | **frame** | one 1024 px render |
| Unit, adapter | **imprint** | one fitted style adapter |
| Unit, motion | **reel** | one short clip |
| Adapter trigger prefix | `jinx_` | e.g. `jinx_film` |

Banned from any customer-facing text: the model names, the checkpoint filenames, the graph
filenames, node class names, the word *uncensored*, and the names of the cloud and hardware
vendors. Components are ingredients, not the brand — and several of those words are other
companies' marks.

## Line

**JINXAI** — portraits from the outer rim.

In order of preference:

1. `JINXAI ORBITAL. Send a signal, get a portrait.`
2. `JINXAI ORBITAL. One line in, one frame out.`
3. `ORBITAL CORE. Your own deep-field deck, without the deck.`

Voice: dry, specific, no exclamation marks, no "unleash", no "revolutionary". Numbers over
adjectives — "8 steps, 33 seconds" beats "blazing fast". The galaxy is the metaphor, not the
sales pitch: no "cosmic", no "stellar", no "infinity".

## Marks

A wordmark is enough before the first paying user; do not commission an icon yet.

- Wordmark: `JINXAI` in a tight grotesque, letterspacing −2%, all caps, no outline.
- Lockup: `JINXAI` over `ORBITAL` at 40% size, letterspacing +18%.
- Clear space: the height of the `J`, every side. Minimum 96 px wide on screen, 18 mm in print.
- Dark: Starlight on Void. Light: Void on Starlight. No gradient, no glow, no drop shadow —
  a glow reads as a filter, not a brand.

## Palette

| Role | Hex | Use |
|---|---|---|
| Void | `#0B0B0C` | background, text on light |
| Starlight | `#F2EDE4` | text on Void, page background |
| Solar Flare | `#E2542C` | one accent: primary action, active tab, the mark |
| Dust | `#6E6A63` | secondary text, rules, captions |
| Landing Light | `#3FA34D` | success and "ready" states only |

Two colours per screen plus neutrals. The frames are the colour; the chrome stays quiet.

## Type

- Display: Inter Tight or Geist, 600/700.
- Body: Inter or system UI, 400/500.
- Captions: 12 px, `+0.06em`, Dust.
- Numbers in tables: tabular figures, right-aligned. Cost and margin are the pitch.

## Domain and handles (status: unconfirmed)

- Primary: `jinxai.<tld>` — confirm what is registered before printing anything.
- Until a custom domain exists the app answers on a vendor-shaped URL; that is not a brand,
  it is a placeholder. See `modal_release.md` for what a branded domain costs.
- Claim the `jinxai` handle only on platforms you will actually post to.

## The one visible exception

Weight files are resolved by literal filename, so a downloaded graph still shows them in its
loader widgets. That is a functional requirement, not a branding choice. Everything the
customer reads — title, heading, footer, listing, tier names, node titles, briefings, output
prefixes — carries the galactic scheme. If you want the weight names gone too, the graph must
point at renamed copies on the host, and every loader widget updated to match.

## Guardrail

JINXAI-branded public surfaces are SFW. That is commercial as much as ethical: the platform
incentive pays only for "positive and healthy" original work, app stores reject adult apps,
and payment processors treat adult content as a different risk class. The unfiltered path
stays available behind a key under ORBITAL CORE, never in a listing or a thumbnail.

## Before anything ships

- [ ] `JINXAI` spelled and capitalised the same way everywhere.
- [ ] No model, file, node or vendor name on a customer-facing page.
- [ ] Solar Flare used once per screen, never as a gradient.
- [ ] Every price matches `pricing.md`.
- [ ] Wordmark legible at 96 px.
