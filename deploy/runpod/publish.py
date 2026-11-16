"""Build, push and register the RunPod public worker — one command, dry-run by default.

Launching on RunPod is four actions: build the image, push it somewhere RunPod can pull from,
register a serverless template that names it, and create an endpoint from that template. This
script does all four, in that order, and does nothing until it is told to.

    python deploy/runpod/publish.py                     # print the payloads, change nothing
    python deploy/runpod/publish.py --apply \
        --image ghcr.io/<you>/velvet-runpod:1 \
        --volume-id <network-volume-id>

Why dry-run is the default: the two POSTs create billable objects on a real account, and a
mistyped GPU list is a monthly bill, not a typo. Read the payload, then apply.

The API calls follow RunPod's REST v1 reference (`POST /v1/templates`, `POST /v1/endpoints`).
They are exercised against the live API for the first time on the first real apply, so the
script prints the response verbatim instead of pretending it knows the shape.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "https://rest.runpod.io/v1"

# 24 GB is the measured floor for stock Krea 2 fp8: 12.3 GB DiT + 4.9 GB encoder + VAE. H100
# is deliberately absent — this host draws commodity frames, and an H100 there costs more than
# the H100 it is meant to replace.
#
# Pool IDs, not device names: a serverless endpoint selects hardware by pool ("AMPERE_24" =
# L4 / A5000 / 3090), and that is all `saveEndpoint` accepts. Device names such as "NVIDIA L4"
# are a different identifier space and belong only in a Pod request — which is what
# `.runpod/tests.json` builds, hence the device name there. Order is the rent priority.
DEFAULT_GPUS = ("AMPERE_48", "AMPERE_24", "ADA_24")

# RunPod matches allowedCudaVersions exactly against the host driver, so the list has to be
# both wide enough to find a machine and no wider than this image can actually start on. Wide
# is fine; wrong is not: the base image declares `NVIDIA_REQUIRE_CUDA` at its own version, and
# a host on an older driver is rejected by the container prestart hook with
# "unsatisfied condition: cuda>=X.Y" — so those entries are hosts that can never run us. The
# base is now 12.6.3 (the lowest Ubuntu 24.04 CUDA image), so 12.6 is the floor here; the
# cu128 torch wheels run on it under minor version compatibility. Keep this list aligned with
# the Dockerfile's FROM line — selftest.py enforces exactly that.
CUDA_VERSIONS = ("13.0", "12.9", "12.8", "12.7", "12.6")

# 40 GB of container disk holds the CUDA image and torch; the 12 GB of weights live on the
# network volume, mounted at the convention the README documents.
CONTAINER_DISK_GB = 40
VOLUME_MOUNT = "/runpod-volume"


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name) or default).strip()


def request(method: str, path: str, key: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + key)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc


def template_payload(name: str, image: str, weights: str, hf_home: str) -> dict:
    return {
        "name": name + "-template",
        "imageName": image,
        "isServerless": True,
        "containerDiskInGb": CONTAINER_DISK_GB,
        "volumeMountPath": VOLUME_MOUNT,
        "env": {
            # The fp8 file is dequantised once to <name>.bf16.safetensors beside it by
            # checkpoint.py; pointing at the fp8 file is correct and expected.
            "OSS_TURBO": weights,
            "HF_HOME": hf_home,
            "PYTHONUNBUFFERED": "1",
        },
    }


def endpoint_payload(name: str, template_id: str, gpus, workers_max: int,
                     volume_id: str = "") -> dict:
    body = {
        "name": name,
        "templateId": template_id,
        "gpuTypeIds": list(gpus),
        "gpuCount": 1,
        "allowedCudaVersions": list(CUDA_VERSIONS),
        "workersMin": 0,
        "workersMax": max(1, int(workers_max)),
        "idleTimeout": 5,
        "scalerType": "QUEUE_DELAY",
        "scalerValue": 4,
        "executionTimeoutMs": 600000,
    }
    if volume_id:
        body["networkVolumeId"] = volume_id
    return body


def docker(*args: str) -> None:
    print("+ docker " + " ".join(args))
    subprocess.run(["docker", *args], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=_env("RUNPOD_IMAGE", "velvet-runpod:1"))
    parser.add_argument("--name", default=_env("RUNPOD_ENDPOINT_NAME", "jinxai-krea2-public"))
    parser.add_argument("--volume-id", default=_env("RUNPOD_VOLUME_ID"))
    parser.add_argument("--weights", default=_env(
        "RUNPOD_WEIGHTS", VOLUME_MOUNT + "/weights/krea2_turbo_fp8_scaled.safetensors"))
    parser.add_argument("--hf-home", default=_env("RUNPOD_HF_HOME", VOLUME_MOUNT + "/hf"))
    parser.add_argument("--gpu", action="append", default=[])
    parser.add_argument("--workers-max", type=int, default=1)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--apply", action="store_true",
                        help="actually build, push and create the billable objects")
    args = parser.parse_args()

    key = _env("RUNPOD_API_KEY")
    gpus = tuple(args.gpu) or DEFAULT_GPUS
    template = template_payload(args.name, args.image, args.weights, args.hf_home)
    endpoint = endpoint_payload(args.name, "<templateId>", gpus, args.workers_max,
                                args.volume_id)

    print("template ->", json.dumps(template, indent=2))
    print("endpoint ->", json.dumps(endpoint, indent=2))

    problems = []
    if not key:
        problems.append("RUNPOD_API_KEY is not set")
    if not args.volume_id:
        problems.append("--volume-id is not set (the endpoint needs the volume holding the "
                        "weights; create one in the console and upload the three files)")
    if not args.skip_build and not args.image.startswith(("ghcr.io/", "docker.io/", "index.docker.io/")):
        problems.append(f"--image {args.image!r} is a local tag; RunPod cannot pull it. "
                        "Push it first or pass a registry-qualified name")
    if problems:
        print("\nblocked:")
        for problem in problems:
            print("  -", problem)
        print("\nNothing was created. Fix the list above, then re-run with --apply.")
        return 2 if args.apply else 0

    if not args.apply:
        print("\ndry run: nothing was built, pushed or created. Re-run with --apply.")
        return 0

    if not args.skip_build:
        docker("build", "-f", "deploy/runpod/Dockerfile", "-t", args.image, ".")
        docker("push", args.image)

    # Idempotent by name: a re-run finds the existing template instead of stacking duplicates.
    created = None
    for row in (request("GET", "/templates", key) or []):
        if isinstance(row, dict) and row.get("name") == template["name"]:
            created = row
            print(f"[runpod] reusing template {row.get('id')}")
            break
    if created is None:
        created = request("POST", "/templates", key, template)
        print("[runpod] template:", json.dumps(created)[:400])

    template_id = str(created.get("id") or "")
    if not template_id:
        print("failed: the template response carried no id", file=sys.stderr)
        return 1

    endpoint["templateId"] = template_id
    existing = None
    for row in (request("GET", "/endpoints", key) or []):
        if isinstance(row, dict) and row.get("name") == endpoint["name"]:
            existing = row
            break
    if existing:
        print(f"[runpod] endpoint already exists: {existing.get('id')}")
        print("         PATCH it in the console if the image or GPU list changed.")
        made = existing
    else:
        made = request("POST", "/endpoints", key, endpoint)
        print("[runpod] endpoint:", json.dumps(made)[:400])

    endpoint_id = str(made.get("id") or "")
    if not endpoint_id:
        print("failed: the endpoint response carried no id", file=sys.stderr)
        return 1

    print("\nlaunched.")
    print("  endpoint id :", endpoint_id)
    print("  submit      : POST https://api.runpod.ai/v2/" + endpoint_id + "/run")
    print("\nNext:")
    print("  1) confirm it answers:  python deploy/runpod/selftest.py --live "
          f"--endpoint {endpoint_id}")
    print("  2) set the Space secrets RUNPOD_ENDPOINT_ID and RUNPOD_API_KEY, then upload "
          "deploy/huggingface/")
    print("  3) export RUNPOD_ENDPOINT_ID on the studio host so orbit_engine can route to it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
