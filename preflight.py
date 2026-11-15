"""Read-only pre-flight for human LoRA testing.

    ./.venv/Scripts/python.exe preflight.py [URL]

Checks the deployed endpoint without spending GPU time on a render and without
downloading any weights: health, both LoRA catalogues, the refusal paths, and the UI
markup. Exit code 0 when every check passes.

`/api/info` is served by the image worker, so on a cold container this script can sit
for a minute before printing — that is the GPU booting, not a hang.
"""

from __future__ import annotations

import base64
import io
import json
import sys
import time
import urllib.error
import urllib.request

from PIL import Image

URL = (sys.argv[1] if len(sys.argv) > 1 else "https://xzyorb--orbital.us-east.modal.run").rstrip("/")
ROWS: list[tuple[str, bool, str]] = []

EXPECTED_IMAGE_LORAS = {
    "michelle", "breastshape", "bimbo", "titjob", "liza",
    "annai", "lily", "estelle", "natalie", "rachelle", "eyes",
    "laundromat", "steph",
}
EXPECTED_VIDEO_LORAS = {
    "throat-a14b", "throat-low", "throat-h3",
    "bounce-a14b", "bounce-low", "bounce-h3",
    "cumshot-h3", "blowjob-h3",
}
WAN_VIDEO = {"throat-a14b", "throat-low", "bounce-a14b", "bounce-low"}


def call(path: str, body: dict | None = None, timeout: int = 600) -> tuple[object, dict, float]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, headers={"content-type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {}), time.time() - t0
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw), time.time() - t0
        except Exception:
            return exc.code, {"error": raw[:300].decode("utf-8", "replace")}, time.time() - t0
    except Exception as exc:  # noqa: BLE001 - report, do not raise
        return None, {"error": f"{type(exc).__name__}: {exc}"}, time.time() - t0


def check(name: str, ok: bool, detail: str) -> None:
    ROWS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name:<38} {detail}", flush=True)


def skip(name: str, detail: str) -> None:
    ROWS.append((name, True, detail))
    print(f"SKIP  {name:<38} {detail}", flush=True)


def tiny_frame() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (320, 320), (120, 90, 70)).save(buf, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main() -> int:
    print(f"pre-flight against {URL}\n", flush=True)

    code, health, secs = call("/health", timeout=120)
    check("health", code == 200 and health.get("ok") is True, f"{code} {health} ({secs:.1f}s)")

    code, info, secs = call("/api/info")
    if code != 200:
        check("info", False, f"{code} {str(info)[:160]}")
        return finish()
    image = info.get("custom_loras") or {}
    video = info.get("video_loras") or {}
    check("info: image LoRAs", set(image) == EXPECTED_IMAGE_LORAS,
          f"{len(image)} entries, missing {sorted(EXPECTED_IMAGE_LORAS - set(image)) or 'none'}")
    check("info: video LoRAs", set(video) == EXPECTED_VIDEO_LORAS,
          f"{len(video)} entries, missing {sorted(EXPECTED_VIDEO_LORAS - set(video)) or 'none'}")

    image_ready = sorted(k for k, v in image.items() if v.get("available"))
    video_ready = sorted(k for k, v in video.items() if v.get("available"))
    print(f"\nweights in the volume: images {image_ready or 'none'}\n"
          f"                       video  {video_ready or 'none'}\n", flush=True)

    # Every Wan entry must declare the A14B pipeline; every H3 entry must declare none.
    wrong = [k for k, v in video.items() if k in WAN_VIDEO and v.get("models") != ["wan22-a14b"]]
    check("video: Wan entries tagged", not wrong, f"mislabelled: {wrong or 'none'}")
    wrong = [k for k, v in video.items() if k not in WAN_VIDEO and v.get("models")]
    check("video: non-Wan refused", not wrong, f"must have models=[]: {wrong or 'none'}")

    code, data, secs = call("/api/lora/download", {"name": "no-such-lora"}, timeout=120)
    check("download: unknown name", code == 400 and "unknown LoRA" in str(data.get("error", "")),
          f"{code} {str(data.get('error'))[:80]}")

    frame = tiny_frame()
    base = {"prompt": "she looks up", "init_image": frame, "model": "wan22-a14b",
            "frames": 17, "quality": 480, "steps": 4}

    code, data, secs = call("/api/video", {**base, "lora": "bounce-h3"})
    err = str(data.get("error", ""))
    check("video: MiniMax guard", code == 400 and "MiniMax H3" in err, f"{code} in {secs:.1f}s {err[:60]}")

    # Only render an existing file: asking for a missing one would start a 293 MB download,
    # which has no place in a read-only pre-flight.
    if "bounce-a14b" in video_ready:
        code, data, secs = call("/api/video", {**base, "lora": "bounce-a14b"})
        err = str(data.get("error", ""))
        check("video: Wan LoRA renders", code == 200, f"{code} in {secs:.1f}s {err[:70]}")
    else:
        skip("video: Wan LoRA renders", "bounce-a14b not in the volume yet — not tested here")

    page = ""
    try:
        with urllib.request.urlopen(URL + "/", timeout=120) as r:
            page = r.read().decode("utf-8", "replace")
        code = 200
    except Exception as exc:  # noqa: BLE001
        check("ui", False, f"{type(exc).__name__}: {exc}")
        page = ""
    if page:
        marks = {"styleLora": "image LoRA select", "styleGet": "download button",
                 "civitaiToken": "token field", "vlora": "video LoRA select"}
        missing = [label for key, label in marks.items() if key not in page]
        check("ui: controls", not missing, f"missing: {missing or 'none'}")
    return finish()


def finish() -> int:
    failed = [r for r in ROWS if not r[1]]
    print(f"\n{len(ROWS) - len(failed)}/{len(ROWS)} checks passed", flush=True)
    if failed:
        print("failed: " + ", ".join(r[0] for r in failed), flush=True)
        return 1
    print("ready: paste the Civitai token in the UI, press 'Download all', then render one "
          "LoRA at a time.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
