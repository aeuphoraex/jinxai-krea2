#!/usr/bin/env bash
# Turn a freshly launched MimicPC ComfyUI into an address the engine can call.
#
# A MimicPC machine runs ComfyUI on 127.0.0.1:8188 inside their container and exposes the web
# UI, not its API. The engine needs the API, so this opens an outbound tunnel and prints the
# URL to put in MIMICPC_COMFY_URL. Run it *inside* the MimicPC terminal, once per session.
#
#   bash deploy/mimicpc/bootstrap.sh            # in the MimicPC terminal
#   export MIMICPC_COMFY_URL=https://<printed host>   # on this machine
#
# The machine bills per hour until it is stopped — stop the app in the MimicPC dashboard when
# the batch is done, or the rent runs while nothing renders.
set -euo pipefail

COMFY_DIR="${COMFY_DIR:-/workspace/ComfyUI}"
COMFY_PORT="${COMFY_PORT:-8188}"

echo "== comfyui directory: $COMFY_DIR"
if [ ! -f "$COMFY_DIR/main.py" ]; then
  echo "!! no ComfyUI at $COMFY_DIR — set COMFY_DIR to the app's install path" >&2
  exit 1
fi

# cloudflared: free, outbound-only, no port forwarding, no account needed for a quick tunnel.
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "== installing cloudflared"
  curl -fsSL -o /tmp/cloudflared \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x /tmp/cloudflared
  mv /tmp/cloudflared /usr/local/bin/cloudflared
fi

if ! curl -fsS -m 5 "http://127.0.0.1:${COMFY_PORT}/system_stats" >/dev/null 2>&1; then
  echo "== starting ComfyUI on ${COMFY_PORT}"
  # --listen is required: a tunnel connects to the loopback port from another process
  nohup python "$COMFY_DIR/main.py" --listen 127.0.0.1 --port "$COMFY_PORT" \
    >/tmp/comfyui.log 2>&1 &
  for _ in $(seq 1 60); do
    sleep 2
    curl -fsS -m 5 "http://127.0.0.1:${COMFY_PORT}/system_stats" >/dev/null 2>&1 && break
  done
fi

echo "== checking the models this pipeline needs"
python - "$COMFY_DIR" <<'PY'
import os, sys
root = os.path.join(sys.argv[1], "models")
need = [
    ("diffusion_models", "krea2_turbo_fp8_scaled.safetensors"),
    ("text_encoders", "qwen3vl_4b_fp8_scaled.safetensors"),
    ("vae", "qwen_image_vae.safetensors"),
]
missing = [f"{sub}/{name}" for sub, name in need
           if not os.path.exists(os.path.join(root, sub, name))]
print("   missing:", missing if missing else "none")
PY

echo "== opening tunnel (leave this running; Ctrl-C closes the API)"
cloudflared tunnel --url "http://127.0.0.1:${COMFY_PORT}" --no-autoupdate 2>&1 \
  | tee /tmp/tunnel.log | grep --line-buffered -o 'https://[a-z0-9-]*\.trycloudflare\.com'
