# RunPod Serverless build entrypoint for the PUBLIC Krea 2 worker.
#
# This file exists at the repo root because a GitHub-connected RunPod build, and the RunPod
# Hub, look for `Dockerfile` there. It is intentionally identical to `deploy/runpod/Dockerfile`,
# which is what `deploy/runpod/publish.py` builds locally — keep the two in sync, and prefer
# this one when changing dependencies so the CI image and the local image cannot drift.
#
# The studio deck (uncensored merge, abliterated encoder, adapters, 4K, Wan video) is NOT in
# this image. This is the stock-core public line only.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HUB_ENABLE_HF_TRANSFER=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    PYTHONPATH=/opt:/opt/krea2 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3.12-dev python3-pip git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* && ln -sf /usr/bin/python3.12 /usr/local/bin/python

# A venv, not --break-system-packages. Ubuntu 24.04 marks its Python as PEP 668 externally
# managed, and its pip 24.0 came from dpkg with no RECORD file, so even `pip install --upgrade
# pip` dies with "Cannot uninstall pip 24.0". Both problems vanish inside a venv, whose pip owns
# its own RECORD. torchvision comes from the same cu128 index because transformers'
# AutoProcessor reaches for it while building the Qwen3-VL processor: without it the first job
# dies at AutoVideoProcessor rather than at build time, which is a much worse place to find out.
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/python -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cu128 torch torchvision \
    && /opt/venv/bin/python -m pip install --no-cache-dir \
        transformers==4.57.1 safetensors>=0.4.0 pillow>=10.0.0 einops>=0.7.0 \
        diffusers>=0.32.0 accelerate>=1.0.0 sentencepiece click hf_transfer runpod

COPY krea2/ /opt/krea2/
# One source of truth for the age rule: the same module the Modal worker imports.
COPY orbit_guard.py /opt/orbit_guard.py
COPY deploy/runpod/checkpoint.py /opt/checkpoint.py
COPY handler.py /opt/handler.py

WORKDIR /opt
CMD ["/opt/venv/bin/python", "-u", "/opt/handler.py"]
