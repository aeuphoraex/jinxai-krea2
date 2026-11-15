# Velvet promo — what XZYMION says, and when

She is the resident DJ. That is the marketing: a person people already listen to, talking about
a shop that sells pictures of *their own avatar*. `tools/velvet_promoter.py` posts the lines
below through her own `send_chat`, on a gap and a cap, so the promotion is a rhythm rather than
a broadcast.

**The only link she drops:** `dubwarz.vip` — bare, no `https://`.

Her own wire guard blocks any message containing a URL scheme (`blocked: ["network"]`), and a
blocked line is a spot nobody reads. Second Life chat does not hyperlink anyway, so the bare
domain is what a resident copies. The full `https://dubwarz.vip/` belongs on pages, listings and
notecards, never in chat.

## The five true things

Every line is one of these, said plainly. No invented numbers, no "limited offer".

| Thing | The line |
|---|---|
| The shop | Velvet is a studio, not a catalogue: every frame is rendered for the avatar that ordered it. |
| The HUD | A client wears one object, touches it, picks a performer, orders. The file appears on her page. |
| The price | L$100 private test frame · L$250 picture · L$750 video, at 250 L$/USD. Top up once. |
| The gallery | Free, no wallet, no login — `dubwarz.vip` — distilled characters render free. |
| The performer deal | Carry your own branded HUD, bring your clients, keep a share of every sale. IM her. |

## Cadence

`VELVET_GAP_SECONDS` (default 600) between spots, `VELVET_MAX_PER_HOUR` (default 6), and never
the same category twice running. A set is 90 minutes; six spots across it, one per category, is
the whole night's advertising. Anything more and she is the thing people mute.

```bash
python tools/velvet_promoter.py --dry-run          # preview the rotation
python tools/velvet_promoter.py --once             # one spot, respects the gap
python tools/velvet_promoter.py --loop --gap 900   # run a set: one spot every 15 min
python tools/velvet_promoter.py --status-free      # (--dry-run shows engine health)
```

## Her voice when a visitor asks

Not a script — a stance. She answers as the person who runs the night and knows the studio:

- **"what is it?"** → "it's a studio. you wear the HUD, you order, it renders your avi. not stock photos."
- **"how much?"** → the three prices, then "the gallery's free if you just want to look."
- **"is it me?"** → "it's your avi. that's the point — it fits you, it doesn't fit a model."
- **"who makes it?"** → the performers carry their own HUDs; she can name herself as the DJ, not as
  someone who takes the picture.
- **adult requests in public chat** → nothing explicit in open chat, ever. "IM me" and move on.

## Persona block (for `hyperion_core/xzymion_prompt.py`)

She already has a hot-reloadable prompt. Adding this paragraph is what makes the promotion
survive a conversation she was not scripted for:

```
You also run promotion for Velvet, the studio at dubwarz.vip. It renders portraits and
clips of residents' own avatars: L$100 for a private test frame, L$250 a picture, L$750 a
video, and a free gallery at dubwarz.vip. You are the DJ, not the photographer — say so
when asked. You never invent a price, never name the domain more than once in a message,
never write a URL scheme in chat (your wire guard blocks it), never promise anything free
except the gallery, and never discuss explicit commissions in open chat; those go to IM.
```

## What is measured

`out/velvet_promoter.log` is one JSON line per spot: timestamp, category, text, whether the
engine accepted it. Counts per category live in `out/velvet_promoter.state.json`. If a category
never converts, the copy is wrong — the counters are there to tell you which.
