# Running the same frame on four paid hosts

One graph, one price list, four kinds of bill. `orbit_engine.py` picks the host for each order;
these folders make each host able to take one.

| Host | What it is | Bill | Idle cost | Env to enable |
|---|---|---|---|---|
| `modal` | this deployment, own diffusers pipeline | **$0.001097/GPU-s** (verified, H100) | 300 s of warm GPU after the last order | always on |
| `runninghub` | managed ComfyUI workflow API | **$0.02/task** (placeholder — set to your credit rate) | none | `RUNNINGHUB_API_KEY`, `RUNNINGHUB_WORKFLOW_ID` |
| `mimicpc` | rented ComfyUI (L4/A10) you keep alive | **$0.000358/s** = $1.29/h instant L4 | the whole machine, until stopped | `MIMICPC_COMFY_URL` |
| `runpod` | serverless worker, scale to zero | **$0.000310/s** (placeholder — set to invoice) | none | `RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID` |
| `huggingface` | free CPU Space in front of the **RunPod public worker** | **$0** | none | `RUNPOD_ENDPOINT_ID` (calls back, never renders) |

Rates flagged *placeholder* are somebody's list price, not a measured invoice: the ledger is
wrong until `ENGINE_RATE_<ID>` / `ENGINE_TASK_USD_<ID>` is set to what is actually charged.
Check the current table with:

```bash
./.venv/Scripts/python.exe tools/engine_check.py --rates
```

## The routing rule

`orbit_engine.choose(job, price)` returns the **cheapest configured render host whose estimated
cost still leaves `ENGINE_MIN_MARGIN_BPS` (default 55%) of gross**. With no keys set, that is
always `modal`, so an unconfigured deploy behaves exactly as it did before. A host that fails
mid-order does not cost the client anything: `commission_render` catches it, records
`engineFallback`, and draws the frame locally instead of refunding.

Every finished order books `priceCents / costCents / marginCents` into `orbital-ledger` under
`engine:<order_id>`, and `GET /api/engine` returns the live table plus what 1,000 pictures would
earn on each host.

## Why a Space is in the list but never draws

A free Space has no GPU worth a 12B model. It is the **front desk**: the visitor types a prompt
in the Space, the Space submits a job to the RunPod public worker, and the frame comes back.
That costs nothing per call and puts the product on an audience that will never find a
`modal.run` URL.

It deliberately does **not** call `/aigf/api/generate`. That route is the studio: uncensored
merge, abliterated encoder, house adapters always fitted. A public Space in front of it is a
content-policy problem on the platform hosting it for free, so the Space talks only to the
public line — stock Krea 2 Turbo, no adapters, `orbit_guard` age rule at the worker.
