# Releasing on Modal: app or workflow?

**Short answer: yes to both, and half of it is already done.** `app.py` is a Modal App that
is deployed and persistent (`modal deploy`), served at
`https://xzyorb--orbital.us-east.modal.run` by a `@modal.asgi_app(label="orbital")`. It already
uses a Modal workflow primitive too: the per-image LoRA fit is a spawned call
(`Krea2LoRA().fit.spawn(...)` in `generate`), so the fit runs in parallel with the render
instead of after it.

What "release" adds is not compute — it is **a gate and a bill**. Modal has no marketplace
and no customer billing: nobody discovers your app through Modal, and nobody can pay you
through it. You publish a URL, you control access, and you invoice elsewhere.

## What exists right now

| Surface | Reality |
|---|---|
| Modal App | deployed, stable URL, `max_containers=3` on the web tier |
| Web UI | `GET /` serves `ui.html` |
| JSON API | `/health`, `/api/info`, `/api/generate`, `/api/video`, `/api/lora/*`, `/api/lora/{job}` |
| Queueing | GPU workers are `max_containers=1`, `max_inputs=1` — requests queue rather than OOM |
| Batch | `.spawn()` for the LoRA fit; `.spawn_map()`/`.map()` are available and unused |
| Auth | **none.** Every route is open to the internet |

Modal's own defaults matter here: Web Functions (`fastapi_endpoint`, `asgi_app`, `wsgi_app`,
`web_server`) are **public by default**, while Endpoints and Servers require auth by default.
So the current public state is the documented default, not a mistake in the code — it just
was never a release decision.

## What a paid release needs

1. **Access control.** Modal proxy tokens: add `requires_proxy_auth=True` to the decorator;
   Modal's edge then rejects anything without `Modal-Key`/`Modal-Secret` **before a container
   starts**. Create tokens with `modal workspace proxy-tokens create`, or in dashboard
   settings. Good for machine clients, useless for a browser (see below).
2. **An auth that a browser can use.** Proxy headers are not something a normal user types, so
   a public-facing UI needs its own key: a `ORBITAL_ACCESS_KEY` in a Modal Secret, checked on
   `/api/*`, entered once in the page and kept in `localStorage`.
3. **Billing outside Modal.** Stripe (or similar) issues keys; your app checks them. Modal
   charges you per second, so every free request is your money.
4. **A domain.** Custom domains are **Team and Enterprise only** ($250/mo on Team). On Starter
   you live at `xzyorb--orbital.us-east.modal.run` — fine for an API, poor for a product.
5. **A usage policy you can survive.** This deployment has no output filter by design; that is
   incompatible with a public listing and with an open signup. Public surface ⇒ SFW app with
   moderation; the NC pipeline stays key-gated.

## The cost ceiling that makes this safe-ish

Verified on Modal's pricing page (Nov 2026): H100 SXM5 is **$0.001097/GPU-second =
$3.9492/hour**. Billing is per second, no minimum, and idle containers are what actually
drain a Starter account.

The project's own caps bound the damage of a public URL: one image worker at
`max_containers=1` plus one video worker.

```
2 × H100 × $3.9492/hr = $7.90/hr ≈ $190/day   # worst case, someone loops the endpoint
```

That is the real argument for `max_containers=1` staying. Without it, Starter's 10-GPU
concurrency would allow `10 × $3.95 ≈ $39.49/hr ≈ $948/day`. On Starter's $30 monthly
credit, the realistic exposure is a card charge, not ruin — but only because of that cap.

Volumes are $0.09/GiB-month with 1 TiB free — the 5.7 GB LoRA volume is ~$0.51/month.
Starter also gives 1 TiB/month free egress; images and clips count toward it.

## Three release shapes, cheapest first

### A. Private app (today, ~15 minutes)

Best for: you plus a handful of clients. No billing, no policy problem.

```python
@modal.asgi_app(label="orbital", requires_proxy_auth=True)
```

```bash
modal workspace proxy-tokens create          # wk-… / ws-…
curl -H "Modal-Key: wk-…" -H "Modal-Secret: ws-…" \
     https://xzyorb--orbital.us-east.modal.run/api/info
```

The browser UI stops working at this point (it cannot send those headers); use the API, or add
shape B's key check for the page and keep proxy auth for automation.

### B. Public SFW app with your own keys

Best for: selling to strangers. This is the real product build.

- `ORBITAL_ACCESS_KEY` secret + per-customer keys in a `modal.Dict`, checked on `/api/generate`,
  `/api/video`, and **all** `/api/lora/*` routes.
- Refuse `/api/lora/token` writes without a key — right now **anyone can overwrite the stored
  Civitai token**, and blanking it breaks downloads for everyone.
- Per-key rate limit (images/hour) enforced in the web tier before `.remote()`.
- Swap the uncensored weight set for the SFW merge, or gate the NC paths behind an admin key.
- Front it with a real domain when you go Team.

### C. Workflow product (batch)

Best for: agencies feeding 50–500 prompts at once, where 33 s each is fine and nobody wants to
hold a socket open. Modal's semantics support it directly: async calls are durable and results
are kept **7 days**, whereas a synchronous call is cancelled within ~2 minutes of the caller
disconnecting. Max timeout per call is 24 h.

Add to the web tier:

```python
@api.post("/api/jobs")                     # submit
def submit(payload: dict = Body(...)):
    return {"id": service.generate.spawn(payload).object_id}

@api.get("/api/jobs/{job}")                # poll
def poll(job: str):
    from modal.functions import FunctionCall
    try:
        return {"done": True, "result": FunctionCall.from_id(job).get(timeout=0)}
    except TimeoutError:
        return {"done": False}
```

For a prompt set, `service.generate.spawn_map(prompts)` submits all of them and returns
immediately — that is the "workflow" in Modal's sense, and it is a better fit for batch than
one long request per image. Persist `{job_id: status}` in a `modal.Dict` if you want the queue
visible after a restart; the `FunctionCall` ids alone are enough for a day.

## Verify before charging anyone

- [ ] `curl` without credentials returns 401 from the Modal edge (proxy auth) — check the
      dashboard shows **no container** was started.
- [ ] With a key, `/api/generate` returns an image, and `/api/info` reports 13/13 + 8/8.
- [ ] An invalid key on `/api/lora/token` is refused, and a valid one still stores the token.
- [ ] `/api/jobs` returns an id that `/api/jobs/{id}` resolves within 60 s.
- [ ] Watch Modal's usage graph for one hour of idle: confirm `scaledown_window` is what you
      think (300 s image, 180 s video, 600 s web) and that nothing stays warm.
- [ ] Set a spending limit in the workspace if Modal offers one for your plan.

## Blockers that are not code

1. **Billing rail.** The platform invoices you; it does not invoice your customers. A payment
   processor plus a key-issuing flow has to exist before shape B.
2. **Custom domain.** Team plan, $250/mo. Until then the URL is `xzyorb--…modal.run`.
3. **Policy.** An unfiltered public endpoint invites content you cannot host. Modal's
   acceptable-use terms apply to you, not to your users.
4. **Licence.** The base checkpoint's community licence permits commercial use but obliges the
   operator to prevent illegal material, and above a seat threshold requires an enterprise
   licence from the rights holder. Seats are not API users, but read it before a big rollout.

## Bring your own key (per deployment)

No credential is committed to the source. Each deployment supplies its own:

```bash
modal run app.py --lora-token=<key>     # writes loras/.civitai_token (0600) in YOUR volume
```

or, once the app is up:

```bash
curl -X POST "$URL/api/lora/token" -H 'content-type: application/json' -d '{"token":"<key>"}'
```

Resolution order is `CIVITAI_TOKEN` (secret or env) → the volume file → an opt-in
`CIVITAI_TOKEN_DEFAULT`, which ships empty. Volumes are per workspace, so two accounts
running this repo never see each other's key, and the file is 0600 and never returned by any
route.

The mutating routes (`/api/lora/token`, `/api/lora/download`, `/api/lora/download_all`) accept
`X-Access-Key` or `Authorization: Bearer` when `ORBITAL_ACCESS_KEY` is set. `GET
/api/lora/token` reports whether it is set and never echoes the key. Turn the gate on before
the app is public:

```bash
export ORBITAL_ACCESS_KEY=$(openssl rand -hex 16)
modal deploy app.py
```

That gate also covers clearing the key — without it, anyone who finds the URL can blank the
stored token and break downloads for you. The value reaches the container because `app.py`
forwards the exported variables into a Modal Secret on the web function; exporting alone
would do nothing.

The browser's **Save** button does not send the header yet, so with the gate on, store or
rotate the key with the CLI/curl form above rather than the UI.
