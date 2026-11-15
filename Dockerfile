# RunPod Serverless build entrypoint for the PUBLIC Krea 2 worker.
#
# This file exists at the repo root because a GitHub-connected RunPod build looks for
# `Dockerfile` there. It is intentionally identical to `deploy/runpod/Dockerfile`, which is
# what `deploy/runpod/publish.py` builds locally — keep the two in sync, and prefer this one
# when changing dependencies so the CI image and the local image cannot drift.
#
# The studio deck (uncensored merge, abliterated encoder, adapters, 4K, Wan video) is NOT in
# this image. This is the stock-core public line only.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HUB_ENABLE_HF_TRANSFER=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    PYTHONPATH=/opt:/opt/krea2

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3.12-dev python3-pip git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* && ln -sf /usr/bin/python3.12 /usr/local/bin/python

# `click` is imported at the top of krea2/inference.py; without it the worker dies at import
# and the failure looks like a crashed container rather than a missing dependency.
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cu128 torch \
    && python -m pip install --no-cache-dir \
        transformers==4.57.1 safetensors>=0.4.0 pillow>=10.0.0 einops>=0.7.0 \
        diffusers>=0.32.0 accelerate>=1.0.0 sentencepiece click hf_transfer runpod

COPY krea2/ /opt/krea2/
# One source of truth for the age rule: the same module the Modal worker imports.
COPY orbit_guard.py /opt/orbit_guard.py
COPY deploy/runpod/checkpoint.py /opt/checkpoint.py
COPY handler.py /opt/handler.py

WORKDIR /opt
CMD ["python", "-u", "/opt/handler.py"]
