# Launch pack — JINXAI ORBITAL

Everything needed to publish the portrait app, plus an honest account of what cannot go
there and what the private deck is for.

| File | Purpose |
|---|---|
| `app_spec.json` | the app: sampler, aspect presets, adapter stack, inputs, prompt template |
| `check_app_spec.py` | fails the launch if the sampler drifts from the core's contract, or an adult adapter is in the stack |
| `listing.md` | title, description, tags and showcase prompts for the publish form |
| `brand.md` | the galactic naming scheme, palette and the guardrail on what may carry the brand publicly |
| `pricing.md` | COMET / PULSAR / QUASAR, unit costs, margins, break-even |
| `economics.md` | measured render cost and the marketplace payout terms |
| `modal_release.md` | gating the private endpoint, batch jobs, abuse ceiling |
| `network.md` | peer table, gossip, least-loaded forwarding, usage ledger, Stripe split |
| `marketplace.md` | signed adapter listings, thumbnails, announce-to-peer replication |
| `dubwarz.md` | the `/gallery` page, domain wiring, packs for sale, processor reality |
| `distro_profit.md` | five hosts, two products: platform permission + rates, QA economics, credit membership |

```bash
python launch/check_app_spec.py    # run before touching the publish form
```

## The decision that shapes the launch

The platform's creator incentive pays for Original Models, AI Apps and AI Characters that are
100% original and have a *positive and healthy creative direction*. The unfiltered deck and
every adult adapter fail that, so there are two products and only one is a listing:

1. **JINXAI ORBITAL — the public app.** The stock core, 0–2 SFW adapters, the prompt
   structure in `app_spec.json`, and the `orbital_portrait` graph. Earns incentive points and
   subscriptions.
2. **ORBITAL CORE — the private deck.** Unfiltered merge, adult adapter stack, prompt
   expansion, per-frame adapter fitting, motion reels. Runs on the private endpoint, sold as
   API access, never uploaded under the brand's public account.

Publishing the second would fail review at best and put the account at risk at worst. Nothing
in this pack does that.

## Checklist

**Account**
- [ ] Account created and verified.
- [ ] Personal Center → **Incentive Program** → accept the participation rules.
- [ ] Confirm current eligibility and settlement in the dashboard; the figures in
      `economics.md` are dated November 2026 and quoted from the platform's own pages.

**Assets**
- [ ] One or two **SFW style adapters**, uploaded and public. Record each activation tag
      verbatim — it must equal `trigger` in `app_spec.json`.
- [ ] 6 showcase frames at 1024² (1:1, 4:5, 16:9 at least), SFW.
- [ ] A cover frame, 1:1, readable at thumbnail size.
- [ ] `python launch/check_app_spec.py` reports PASS.

**Graph**
- [ ] Import `comfy/orbital_portrait.json` into the host's canvas.
- [ ] Replace the video saver node if the host lacks it.
- [ ] Set both `LoraLoaderModelOnly` nodes to the SFW adapters, or `None`.
- [ ] Mute the echo branch unless the app accepts an image.
- [ ] Render one frame on the host and compare it to a local one.

**Publish**
- [ ] Create → Workflow or AI App → **Publish**.
- [ ] Graph information: title, description, node list.
- [ ] App information: copy from `listing.md`, inputs in `app_spec.json` order.
- [ ] Mark **Original** (the incentive pays only on Original); public earns, private does not.
- [ ] Use the app once as a normal user and check the input form.

**After launch**
- [ ] Track valid generations and unique users, not likes.
- [ ] Add `comfy/orbital_motion.json` as a second app only if the motion path is SFW.
- [ ] Re-check incentive terms monthly; the structure has changed before.

## What is genuinely missing

1. **An SFW adapter choice.** The registry in this repo is adult-only. The app ships fine with
   zero adapters — the core alone is a complete product — or you pick one on the platform.
2. **Showcase frames.** None exist in SFW form; everything generated locally used the
   unfiltered stack.
3. **A confirmed node set on the host.** The motion saver is flagged experimental upstream and
   the zeroed-negative node may be absent; swap to a plain empty negative if so.
4. **Incentive review.** Joining is self-serve, but payment depends on acceptance as Original
   and reaching real unique users — no guarantee, and posting alone earns nothing.

## The naming boundary

Customer-facing text carries only the galactic scheme. Two things keep their literal names
because software resolves them by string:

- **Weight filenames** in the graph's loader widgets (`orbital_portrait.json` still lists the
  model files it loads). Renaming them means renaming copies on the host and updating every
  widget to match.
- **The platform's base-model selector** shows the checkpoint's real name — that field belongs
  to the host, not to this pack.

Neither appears in the listing, the page, the tiers, the node titles or the briefings.
