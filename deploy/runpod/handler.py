"""Re-export of the repository's root `handler.py`.

The canonical worker is `/handler.py` at the repository root, because that is the path the
RunPod worker convention uses (`python handler.py`, `test_input.json`, `runpod.serverless.start`).
This module keeps `selftest.py` and the notes that import `deploy/runpod/handler.py` working: it
loads the root file under a private module name, so the two `handler` modules cannot collide.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location("krea2_runpod_handler", ROOT / "handler.py")
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["krea2_runpod_handler"] = _MOD
_SPEC.loader.exec_module(_MOD)

handler = _MOD.handler
pipeline = _MOD.pipeline
STATE = _MOD.STATE
TEST_INPUT = _MOD.TEST_INPUT
STEPS = _MOD.STEPS
CFG = _MOD.CFG

if __name__ == "__main__":
    import runpod

    runpod.serverless.start({"handler": handler, "test_input": TEST_INPUT})
