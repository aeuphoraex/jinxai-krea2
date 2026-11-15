# dubwarz.vip — gallery + model sales

The gallery is built and served from the same deployment:

| Route | What it is |
|---|---|
| `/gallery` | the gallery page (`gallery/index.html`) |
| `/api/gallery` | the catalog (`gallery/catalog.json`) |
| `/api/shop/checkout` | Stripe destination charge for one character pack |
| `/api/generate` | the render behind the **Generate** button |

The gallery is **global**: `GET /api/gallery` returns this node's catalog merged with every
peer's, deduped by `sku` with the local copy winning, and each character carries the `node`
that serves it. Peers are read from the peer table plus `ORBITAL_PEERS`, fetched in parallel
with a 4 s timeout and cached for 60 s; a dead peer is listed in `nodes[]` with its error and
contributes nothing else. `GET /api/gallery?scope=local` is this node alone — that is the
call a peer makes, so the network never recurses. Since `dubwarz.vip` serves the gallery at
`/`, the branded domain is the global view by default.

## Distilled characters (prompt → AI girlfriend)

Any prompt posted to `POST /api/girlfriend/distill {"prompt":"..."}` is distilled into a
gallery character and published to the shared gallery: `orbit_girls.distill` classifies the
text into the catalog's own tags (`pilot`, `navigator`, `officer`, `dockmaster`, `engineer`,
`medic`, `courier`, `scavenger`, `dancer`, `aristocrat`, `crew`), folds each into a group
(`flight deck`, `command`, `dock`, `machine shop`, `infirmary`, `transit`, `derelict`,
`night deck`, `upper ring`, `general`), picks a name from that archetype's pool by prompt
hash, and returns the catalog's exact field set plus `group`, `kind: "distilled"` and
`created_at`. It is deterministic and offline — the same prompt always yields the same
character, with no model call.

Distilled characters live in the `orbital-girlfriends` Dict (shared by every container of the
service, capped at 600, newest first), appear in `GET /api/gallery` as `distilled`, and carry
`price_cents: 0` because there is no pack to sell — the page shows them as *distilled · free*
with a Generate button. `GET /api/girlfriends` lists them alone; `groups[]` on both responses
is the classification summary the page filters on.

Distillation is triggered from the gallery's prompt bar, not from `/api/generate`: auto
publishing every render prompt would put arbitrary visitor text on a public page. Wiring that
in later is one call to the same helper, behind whatever consent the site wants.

Eight characters ship in the catalog with a prompt, seed, aspect and price. Editing
`gallery/catalog.json` and redeploying is the whole content workflow — no database, no CMS.

## Put the domain on it

`dubwarz.vip` is not connected yet; the app answers on a Modal URL.

1. **Fastest:** Cloudflare (or any DNS) `CNAME` for `dubwarz.vip` → the Modal endpoint, with
   the proxy enabled and SSL set to Full (strict). Works today, on any plan. On Cloudflare a
   `CNAME` at the apex needs flattening ("CNAME flattening" is the default there).

**Current DNS state, checked:** the zone is served by `ns1.dyna-ns.net` / `ns2.dyna-ns.net`
(Dynadot), and `dubwarz.vip` has an **A record to `107.179.232.160`**, which reverse-resolves
to `107-179-232-160.cpe.teksavvy.com` — a residential connection with nothing listening on
443. So the domain resolves, but to the wrong machine; visitors currently get a connection
refused, not the gallery. Fix it at Dynadot (or move the nameservers to Cloudflare):

```
dubwarz.vip.      CNAME   xzyorb--orbital.us-east.modal.run.
www.dubwarz.vip.  CNAME   xzyorb--orbital.us-east.modal.run.
```

Delete the A record rather than adding alongside it — an apex A beats a CNAME and the old IP
would keep winning. After the zone updates, `curl -sI https://dubwarz.vip` should answer from
Modal and `GET /` should return the gallery.

### Caddy on this machine (read from the running config)

Caddy is already serving the domain. Read live, via its admin API on `127.0.0.1:2019`
(the `%APPDATA%\Caddy\autosave.json` file claims `admin: None` — that is stale):

| Site | Upstream |
|---|---|
| xzyorbitz.duckdns.org | localhost:8765, localhost:7860, file_server |
| aetheric.duckdns.org | localhost:7860 |
| jinxai.duckdns.org | localhost:7788 (with auth) |
| www.dubwarz.club | 308 redirect |
| **dubwarz.vip, www.dubwarz.vip** | **127.0.0.1:8792** (wrong target) |

Certificates exist under `%APPDATA%\Caddy\certificates\acme-v02.api.letsencrypt.org-directory\`
for both `dubwarz.club` and `dubwarz.vip` (the `dubwarz.vip` pair is dated 9 Nov, issued before
the process wedged).

#### State on 2026-11-15: Caddy is wedged and needs a restart

`caddy.exe` (PID 4760, session `Services`) still holds `:80`, `:443` and `:2019` in `netstat`,
but answers nothing: HTTP and HTTPS time out on loopback (`127.0.0.1`), on both LAN addresses,
and on the public IP, and the admin API (`127.0.0.1:2019/config/`) returns `000`. Because the
admin API is unreachable, `--apply` below cannot load anything until the process is restarted.

Nothing was lost: `%APPDATA%\Caddy\autosave.json` still carries its 9 Nov timestamp (14,303
bytes, servers `srv0`/`srv1`/`srv2`), so the failed `/load` never wrote to disk.

The binary is not on `PATH` and the wedged process runs in session 0 as SYSTEM, so an
unelevated `taskkill` is refused. A Caddy v2.11.9 binary now lives in the project
(`tools/caddy/caddy.exe`), and `tools/caddy/run_config.json` is the saved autosave config
with the repoint applied plus `admin: 127.0.0.1:2019` — `caddy validate` passes on it.
Rebuild it after config changes with `python tools/caddy_build_run_config.py`.

**Restart, elevated** (right-click → Run as administrator, or an admin shell):

```bat
cmd /c "F:\MODAL\tools\caddy\restart_caddy.bat"
```

or by hand:

```bat
taskkill /F /IM caddy.exe
start "" /MIN F:\MODAL\tools\caddy\caddy.exe run --config F:\MODAL\tools\caddy\run_config.json
```

Then confirm `http://127.0.0.1:2019/config/` answers 200, and run
`python tools/caddy_point_at_orbital.py --apply` only if you change the target again.

If restarting the way it was originally started instead:

```bash
caddy stop                                  # if that cannot reach it: taskkill /PID 4760 /F
caddy run --config "%APPDATA%\Caddy\autosave.json"
# or standalone with the new block:
caddy run --config caddy/dubwarz.vip.caddyfile --adapter caddyfile
```

Then confirm `http://127.0.0.1:2019/config/` answers 200, and re-run the script below.

Repoint it with the script rather than hand-editing JSON:

```bash
python tools/caddy_point_at_orbital.py --dry-run   # prints the exact diff
python tools/caddy_point_at_orbital.py --apply     # loads via the admin API
```

If you would rather restart with a file than reload through the API, `caddy/dubwarz.vip.caddyfile`
is the same change in Caddyfile form, ready to include or run standalone.

It changes only the `dubwarz.vip` block: upstream → `xzyorb--orbital.us-east.modal.run:443`
with TLS, `Host` set to the Modal hostname, and `/` rewritten to `/gallery` so the domain
opens on the shop. Every other site block is untouched because the whole config is posted
back with one subtree edited. On apply, Caddy requests a certificate for `dubwarz.vip`; that
succeeds only if port 80/443 reach this machine from the internet.
2. **Native:** Modal custom domains are Team/Enterprise only. Verify the domain in workspace
   settings, then bind it at deploy time — no code edit needed:

   ```bash
   export ORBITAL_CUSTOM_DOMAIN=dubwarz.vip
   modal deploy app.py
   ```

`ORBITAL_CUSTOM_DOMAIN` is read at deploy time and passed to `@modal.asgi_app`, so an unset
variable leaves the default Modal URL untouched.

The app is host-aware: a request arriving with a `Host` of `dubwarz.vip` (or any subdomain of
it) is served the gallery at `/`, while the bare endpoint still serves the studio UI. That
means either wiring option above lands visitors on the shop, not the generator panel. Override
the hostname with `ORBITAL_SITE_DOMAIN` if the shop moves.

Either way, add `https://dubwarz.vip` to the allowed origins if you later add CSRF or CORS
handling, and set `ORBITAL_SELF_URL=https://dubwarz.vip` so peers register the branded URL
rather than the Modal one.

## Selling models — what the code does

`POST /api/shop/checkout {"sku":"dw-kessa"}` returns a Stripe `client_secret` built as a
**destination charge**:

```
amount                     = catalog price_cents
application_fee_amount     = platform share
transfer_data[destination] = STRIPE_CONNECTED_ACCOUNT
```

At the default 70% developer share (`STRIPE_DEVELOPER_BPS=7000`), the developer's account is
paid on capture and the platform keeps 30% minus Stripe's processing fee. Without the two
Stripe variables the route answers **503** with the exact setup line and charges nothing.

### The catch that decides the business

**Stripe prohibits adult content.** Not "discourages" — using it for explicit material risks
the account and the funds on it. So:

- The catalog as shipped is `rating: "sfw"`. Selling those through Stripe is fine.
- An `adult` SKU needs a high-risk processor (CCBill, SegPay, Verotel) or a crypto rail.
  Those have their own onboarding, reserves and chargeback terms; expect 5–15% all-in and a
  rolling reserve for the first months.
- Keep the two catalogs separate — different domain sections, different processors, different
  refund policy — so a processor review never sees mixed content.

### Delivery

`/api/lora/{job}/file` serves a fitted adapter once a job exists. That is **not** entitlement:
it is a URL. For paid packs, gate it on a studio `PATREON`-style check or a webhook that marks
`sku` as paid — the honest minimum is `Stripe /webhook → usage_ledger["paid:<sku>"] → signed
download`. Until that exists, treat the checkout as a pre-order and deliver manually.

## What the gallery does not do yet

1. **No persistence of renders.** The page shows the image returned in that request; reload and
   it is gone. Adding a volume-backed library is the next step if people use it.
2. **No accounts.** Nothing ties a render to a buyer. That is why the shop is per-pack.
3. **No content filter.** The footer says renders are unfiltered and the operator owns what
   they publish. For a public domain with adult SKUs, that sentence is not a legal shield —
   you need a written policy and an age gate.
4. **No webhook handler.** Stripe will confirm payment to a URL you must provide; nothing
   listens yet.

## Pricing the packs

At $19 a pack and 70% to the developer, the developer receives $13.30 before Stripe's fee
(which lands on the platform side). One pack is roughly 150 frames of GPU time at the
measured $0.049 blended cost, so a pack sold at $19 with no support burden clears about
$13 — the render cost is negligible next to the price, which is why character packs beat
per-image credits for this audience.

## Sequence

1. Deploy, open `/gallery`, confirm the eight cards render and **Generate** returns an image.
2. Point `dubwarz.vip` at it (CNAME + Cloudflare proxy is enough to start).
3. Set `STRIPE_SECRET_KEY` + `STRIPE_CONNECTED_ACCOUNT`, redeploy, confirm
   `/api/econ/terms` reports `stripe_configured: true`.
4. Add the webhook and a paid-check on the download before the first real sale.
5. Add adult SKUs only after a high-risk processor is live.
