"""Export every Modal Dict this project writes to, as one local JSON file.

The files on disk are not the server: wallets, commissions, gallery blobs, chat memory,
distilled characters, upsell records, market listings and the peer table live in Modal Dicts.
A project copy without them is a code backup, not a server backup.

    ./.venv/Scripts/modal.exe run tools/backup_dicts.py --out backups/dicts-<stamp>.json

Runs in a throwaway app so it never touches the deployed `jinxai-orbital` revision.
"""

import json
import time

import modal

app = modal.App("jinxai-dict-backup")

# name -> "why it exists", so a restore knows what it is looking at
DICTS = {
    "orbital-wallets": "client L$ balances, spend and the resident name pair",
    "orbital-commissions": "every Velvet order: status, price, adapters, steps",
    "orbital-gallery-blobs": "base64 JPEGs served as gallery cards",
    "orbital-gallery-thumbs": "which sku has a rendered card, and when",
    "orbital-girlfriends": "characters distilled from visitor prompts",
    "orbital-upsell": "each character's upgraded personal-gallery record",
    "orbital-listings": "marketplace listings",
    "orbital-chats": "per-visitor chat memory and profile readings",
    "orbital-ledger": "usage/settlement ledger",
    "orbital-peers": "peer node table",
}


@app.function(image=modal.Image.debian_slim(python_version="3.12").pip_install("fastapi>=0.115.0"))
def dump() -> dict:
    out = {"exported_at": int(time.time()), "dicts": {}}
    for name, note in DICTS.items():
        try:
            store = modal.Dict.from_name(name, create_if_missing=True)
            keys = list(store.keys())
            rows = {}
            for key in keys:
                try:
                    rows[str(key)] = store.get(key)
                except Exception as exc:  # one bad value must not sink the backup
                    rows[str(key)] = {"__unreadable__": f"{type(exc).__name__}: {exc}"}
            out["dicts"][name] = {"note": note, "count": len(rows), "items": rows}
        except Exception as exc:
            out["dicts"][name] = {"note": note, "error": f"{type(exc).__name__}: {exc}"}
    return out


@app.local_entrypoint()
def main(out: str = ""):
    data = dump.remote()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    path = out or f"backups/dicts-{stamp}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    total = sum(int(v.get("count") or 0) for v in data["dicts"].values())
    print(f"wrote {path} · {total} keys across {len(data['dicts'])} dicts")
    for name, row in data["dicts"].items():
        print(f"  {name}: {row.get('error') or str(row.get('count')) + ' keys'}")
