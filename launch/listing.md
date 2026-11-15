# Listing copy — JINXAI ORBITAL

Paste into the publish form. No model, file, node or vendor names anywhere.

## Title

JINXAI ORBITAL

## Tagline (60 chars)

Send a signal. Get a portrait.

## Short description (250 chars)

Describe a figure and a place. ORBITAL writes the photographic prompt and returns a finished
1024 px portrait in 8 steps. Choose an aspect, a look, and how strongly a style adapter
applies. Optional adapter, optional seed.

## Full description

JINXAI ORBITAL turns one line into a finished portrait.

You name who and where — "a navigator with a shaved head, at a viewport above a ring city".
The deck assembles the rest: lens, light, grain, skin texture, focus. You pick the aspect, the
look preset, and the strength of an optional style adapter.

It renders on a distilled 8-step core with guidance off. That is the setting the core was
distilled for: fast, and closer to what it intends than pushing a guidance scale it was never
trained against.

**What you get**
- 1024 px on the short edge, five aspect presets from 1:1 to 9:16
- photographic prompts, not tag soup — the core reads full sentences
- one optional style adapter with an independent strength
- a reusable seed

**Tips**
- Name a subject and an action before adding style words.
- Keep it under two sentences; longer briefs average out the composition.
- Drop the adapter to 0.6 when it overpowers the face.

## Tags

portrait, photography, editorial, orbital, film, natural light, character, style-transfer

## Inputs (order mirrors app_spec.json)

1. **Who or what** — required text, 220 chars
2. **Scene and light** — text
3. **Look** — 35mm film / editorial studio / natural light documentary / high-contrast black
   and white / cinematic teal and amber
4. **Aspect** — 1:1, 4:5, 3:4, 16:9, 9:16
5. **Style strength** — 0.0–1.2, default 0.8
6. **Seed** — -1 for random

## Showcase prompts

| # | Who | Where | Look | Aspect |
|---|---|---|---|---|
| 1 | a navigator with freckles and a blunt bob | station cafe by a tall viewport, soft morning light | 35mm film | 4:5 |
| 2 | an older dockmaster with a weathered face | hangar bay, hard key light from the left | editorial studio | 1:1 |
| 3 | a courier in a rain shell, back to camera | empty landing platform, overcast | documentary | 16:9 |
| 4 | a dancer mid-turn | dark observation deck, single warm spot | high-contrast black and white | 3:4 |
| 5 | a pilot in a linen flight shirt | rooftop of a ring city at golden hour | cinematic teal and amber | 9:16 |
| 6 | a child laughing, motion blur | galley table, morning | 35mm film | 1:1 |

## Keep out of the listing

- Model, checkpoint, graph or node names; the word *uncensored*; vendor names.
- Claims about features the app cannot verify on the host (per-frame adapters, 4K tiling).
- Any reference to the private render endpoint; it is not part of the app.
