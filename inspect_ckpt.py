"""Read safetensors headers over HTTP range requests (no full download)."""
import json
import struct
import sys
import urllib.request

FILES = sys.argv[1:]


def head(url, nbytes=200):
    req = urllib.request.Request(url, headers={"Range": "bytes=0-4194303", "User-Agent": "k2-inspect"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


for spec in FILES:
    url = spec
    print("=" * 100)
    print(url)
    try:
        blob = head(url)
    except Exception as e:
        print("  FETCH FAIL:", repr(e)[:200])
        continue
    if len(blob) < 8:
        print("  too short")
        continue
    (n,) = struct.unpack("<Q", blob[:8])
    try:
        hdr = json.loads(blob[8 : 8 + n].decode("utf-8"))
    except Exception as e:
        print(f"  header parse fail (len {n}):", repr(e)[:120])
        print("  first bytes:", blob[:200])
        continue
    meta = hdr.pop("__metadata__", None)
    print(f"  header bytes={n} tensors={len(hdr)} metadata={str(meta)[:300]}")
    dtypes = {}
    for k, v in hdr.items():
        dtypes[v["dtype"]] = dtypes.get(v["dtype"], 0) + 1
    print("  dtypes:", dtypes)
    keys = list(hdr)
    print("  first 12 keys:")
    for k in keys[:12]:
        print("   ", k, hdr[k]["dtype"], hdr[k]["shape"])
    print("  last 5 keys:")
    for k in keys[-5:]:
        print("   ", k, hdr[k]["dtype"], hdr[k]["shape"])
    # look for LoRA / fp8 scale tensors and prefixes
    for needle in ("lora", "scale", "blocks.0.", "txtfusion", "first.weight", "last.linear"):
        hits = [k for k in keys if needle in k][:4]
        if hits:
            print(f"  [{needle}] ->", hits)
    prefixes = sorted({k.split(".")[0] for k in keys})
    print("  top-level prefixes:", prefixes[:15])
