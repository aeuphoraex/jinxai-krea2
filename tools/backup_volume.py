"""Archive the volume's user data inside Modal, then fetch one file.

`modal volume get <vol> <dir> <local-dir>` cannot create destination directories on this
Windows host (`Permission denied: 'backups\\volume\\gallery'`), so directory pulls are
unusable here. Instead this packs the user-owned parts of `krea2-weights` into a single
tar.gz **on the volume**, prints its size, and the caller fetches that one file.

Model weights are deliberately excluded (they are re-downloadable); this is the data that
cannot be regenerated: her fitted adapters, the gallery thumbs, the commission masters and
the marketplace manifests.

    ./.venv/Scripts/modal.exe run tools/backup_volume.py
    ./.venv/Scripts/modal.exe volume get krea2-weights _backup/<name>.tar.gz backups/<name>.tar.gz
"""

import os
import tarfile
import time

import modal

app = modal.App("jinxai-volume-backup")

weights = modal.Volume.from_name("krea2-weights", create_if_missing=True)
W = "/weights"
INCLUDE = ("loras", "gallery", "market")
# Never inside the archive: per-image LoRA fits are churn, and studio.json is 200 bytes
# copied into the project archive anyway.
SKIP_DIRS = {"loras/per_image", "loras/video"}


@app.function(volumes={W: weights}, timeout=1800)
def pack() -> dict:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    out_dir = os.path.join(W, "_backup")
    os.makedirs(out_dir, exist_ok=True)
    name = f"volume-{stamp}.tar.gz"
    path = os.path.join(out_dir, name)
    added = 0
    with tarfile.open(path, "w:gz") as tar:
        for folder in INCLUDE:
            root = os.path.join(W, folder)
            if not os.path.isdir(root):
                continue
            for base, dirs, files in os.walk(root):
                rel = os.path.relpath(base, W).replace(os.sep, "/")
                if any(rel == skip or rel.startswith(skip + "/") for skip in SKIP_DIRS):
                    dirs[:] = []
                    continue
                for fname in sorted(files):
                    full = os.path.join(base, fname)
                    arc = os.path.join(rel, fname)
                    try:
                        tar.add(full, arcname=arc)
                        added += 1
                    except OSError as exc:
                        print(f"[volume-backup] skipped {arc}: {exc}")
    weights.commit()
    size = os.path.getsize(path)
    studio = os.path.join(W, "studio.json")
    return {"name": name, "bytes": size, "files": added,
            "has_studio_json": os.path.exists(studio)}


@app.local_entrypoint()
def main() -> None:
    got = pack.remote()
    print(f"packed _backup/{got['name']} · {got['bytes'] / 1e6:.1f} MB · {got['files']} files "
          f"· studio.json on volume: {got['has_studio_json']}")
    print(f"fetch: modal volume get krea2-weights _backup/{got['name']} backups/{got['name']}")
