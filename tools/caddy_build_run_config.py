"""Build a runnable Caddy config from the saved autosave file, with dubwarz.vip repointed.

The running Caddy is wedged and its admin API cannot be reached, so the repoint that
`caddy_point_at_orbital.py` would normally POST is applied to the on-disk config instead,
and an admin listener is added (the autosave file says `admin: None`) so future reloads work.

    python tools/caddy_build_run_config.py
    caddy run --config tools/caddy/run_config.json
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import caddy_point_at_orbital as cpa  # noqa: E402

SOURCE = pathlib.Path(os.environ["APPDATA"]) / "Caddy" / "autosave.json"
TARGET = HERE / "caddy" / "run_config.json"


def main() -> int:
    config = json.loads(SOURCE.read_text(encoding="utf-8"))
    hits, notes = cpa.patch(config)
    for note in notes:
        print(" -", note)
    if not hits:
        print("no dubwarz.vip reverse_proxy route found; refusing to write")
        return 1
    config["admin"] = {"listen": "127.0.0.1:2019"}
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(config, indent=2), encoding="utf-8")
    servers = sorted(config.get("apps", {}).get("http", {}).get("servers", {}))
    print(f"wrote {TARGET} (servers {servers}, admin 127.0.0.1:2019)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
