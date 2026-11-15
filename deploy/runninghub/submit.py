"""Submit one frame to a RunningHub workflow and save it.

    RUNNINGHUB_API_KEY=... RUNNINGHUB_WORKFLOW_ID=1904... \
      python deploy/runninghub/submit.py --prompt "a woman in a rain-lit alley" --out out/rh.png

This is the same call `orbit_engine.RunningHub` makes on a paid order, so a prompt that works
here works in production. Node ids come from `node_map.json` (or `RUNNINGHUB_NODES`), because
they belong to the workflow you uploaded.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import orbit_engine as E  # noqa: E402

QUALITY = (
    "raw photo, photorealistic, ultra-detailed natural skin texture with visible pores and "
    "fine vellus hair, realistic subsurface scattering, natural makeup, no plastic or "
    "airbrushed skin, sharp focus on the eyes, 85mm lens, subtle film grain, "
    "clean background bokeh, no motion blur"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=1600)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="out/runninghub.png")
    ap.add_argument("--quality", action="store_true", help="append the studio quality tokens")
    args = ap.parse_args()

    ok, missing = E.configured("runninghub")
    if not ok:
        print(f"missing {missing}")
        return 2
    job = {
        "prompt": args.prompt + (", " + QUALITY if args.quality else ""),
        "width": args.width, "height": args.height, "seed": args.seed, "kind": "image",
    }
    client = E.RunningHub(nodes=E.node_map())
    task = client.create(job)
    print(f"task {task} queued on workflow {client.workflow_id}")
    url = client.wait(task)[0]
    print(f"  -> {url}")
    got = E._fetch_image(url)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(got)
    print(f"saved {args.out} ({len(got) / 1e6:.2f} MB)")
    if got[:4] != b"\x89PNG":
        print("warning: the bytes are not a PNG")
        return 1
    base64.b64encode(got)  # the production path returns base64; prove it is encodable
    return 0


if __name__ == "__main__":
    sys.exit(main())
