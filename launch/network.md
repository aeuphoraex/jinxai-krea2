# ORBITAL network + developer revenue

Two additions: nodes discover each other and hand work to the least-loaded peer, and usage
is metered so a share of gross can be routed straight to JINXAI.

## What is actually implemented

| Route | Auth | Does |
|---|---|---|
| `GET /api/net/peers` | none | self entry + live peer table + `best_image` pick |
| `POST /api/net/register` | cluster token | add/refresh one peer |
| `POST /api/net/gossip` | cluster token | merge a peer list, return your own |
| `POST /api/net/generate` | none (app-level) | render on the least-loaded peer, else locally |
| `GET /api/econ/terms` | none | developer share, unit prices, Stripe readiness |
| `POST /api/econ/record` | cluster token | meter usage and accumulate the split |
| `GET /api/econ/ledger` | none | cumulative gross / developer / platform cents |
| `POST /api/econ/checkout` | app-level | Stripe destination charge (503 without keys) |

State lives in two Modal Dicts: `orbital-peers` and `orbital-ledger`. Entries expire after
15 minutes without a heartbeat, so a stopped node disappears on its own.

## Wiring a cluster

Every node needs an identity and a shared secret:

```bash
export ORBITAL_NODE_ID=orbital-a
export ORBITAL_SELF_URL=https://xzyorb--orbital.us-east.modal.run
export ORBITAL_CLUSTER_TOKEN=$(openssl rand -hex 24)
export ORBITAL_PEERS=https://other-node.modal.run
modal deploy app.py
```

Then introduce them:

```bash
curl -X POST "$B/api/net/register" -H "x-cluster-token: $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"node":"orbital-a","url":"https://xzyorb--orbital.us-east.modal.run","caps":["image","video","lora"],"gpu":"H100","load":0}'

curl -X POST "$A/api/net/gossip" -H "x-cluster-token: $TOKEN" \
  -H 'content-type: application/json' -d '{"peers":[]}'    # returns A's table
```

`POST /api/net/generate` forwards the payload to the lowest-load peer advertising `image`,
and falls back to local rendering if the forward fails — the response says `served_by`.

## The honest limits

- **A Modal Dict is per workspace.** Two different accounts cannot see one table. Cross-account
  discovery is the seed list plus explicit registration, not automatic. Nothing here finds a
  stranger's node, and that is deliberate.
- **No NAT traversal or relay.** A node must be reachable over HTTPS. That covers Modal,
  Cloudflare-fronted hosts, and any VPS; it does not cover a laptop behind a router.
- **Trust is a shared token.** Anyone with `ORBITAL_CLUSTER_TOKEN` can register peers and
  forward jobs. Rotate it like a password; there is no per-peer identity or signing.
- **A job is not split across nodes.** Forwarding sends the whole request to one peer. Real
  sharding of one render across GPUs is a different problem (pipeline parallelism), not this.
- **No billing between peers.** Metering records usage you report; a peer that lies about its
  usage is trusted. If money flows between operators, verify with Stripe webhooks instead.

## Stripe: paying the developer directly

The split is already computed in `orbit_econ.split_cents` (integer cents, developer share
defaults to 70% = `STRIPE_DEVELOPER_BPS=7000`). Checkout builds a **destination charge**:

```
amount              = gross cents
application_fee_amount = platform cents
transfer_data[destination] = connected account
```

That is Stripe's documented shape for "customer pays, seller is paid immediately, platform
keeps a fee". Setup:

1. Create a Stripe Connect platform account, complete the platform profile.
2. Onboard the developer as an **Express** connected account (Stripe-hosted onboarding).
3. Export the three values and redeploy:

```bash
export STRIPE_SECRET_KEY=sk_live_...
export STRIPE_CONNECTED_ACCOUNT=acct_...
export STRIPE_DEVELOPER_BPS=7000
modal deploy app.py
```

4. Verify: `GET /api/econ/terms` → `stripe_configured: true`, then
   `POST /api/econ/checkout {"usage":{"frame":20}}` returns a `client_secret`.

Without those keys the route returns **503 with the instructions** rather than pretending to
charge. That refusal path is the one that is tested; a live charge needs your Stripe account.

### What the split means in practice

On a $1.00 charge at 7000 bps the developer receives 70c and the platform keeps 30c, out of
which Stripe's processing fee (2.9% + 30c on the platform's side for destination charges) is
paid. At $1.00 that fee is ~30.2% of gross — so a $1.00 price at a 70% developer share loses
money on the platform side. Break-even on a destination charge is roughly:

```
platform_cents >= 0.029 * gross + 30
```

which, at 30% kept, is a gross of about **$1.35** before the platform clears anything. Price
per-charge items accordingly, or sell subscriptions and split monthly.

### Compliance, stated plainly

- Money transmission rules vary by country; Stripe Connect exists to keep the platform out of
  that scope, but the platform is still the merchant of record for destination charges.
- Refunds and chargebacks land on the platform balance, not the developer's — price for it.
- The developer must complete KYC with Stripe; you cannot pay them without it.
