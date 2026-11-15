# RunPod serverless worker for the PUBLIC Krea 2 line.
#
# Built from the CUDA runtime image rather than a guessed RunPod PyTorch tag: the tag names in
# registry.runpod.ai change, and a wrong tag fails at `docker build` on the user's machine, not
# at launch time. Python 3.12 + torch cu128 is what krea2/pyproject.toml asks for.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HUB_ENABLE_HF_TRANSFER=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    PYTHONPATH=/opt:/opt/krea2

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3.12-dev python3-pip git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* && ln -sf /usr/bin/python3.12 /usr/local/bin/python

# `click` is imported at the top of krea2/inference.py — without it the worker dies at import,
# not at the first job, and the failure looks like a crashed container rather than a missing dep.
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cu128 torch \
    && python -m pip install --no-cache-dir \
        transformers==4.57.1 safetensors>=0.4.0 pillow>=10.0.0 einops>=0.7.0 \
        diffusers>=0.32.0 accelerate>=1.0.0 sentencepiece click hf_transfer runpod

# The package this deployment already uses; the weights stay on the network volume.
COPY krea2/ /opt/krea2/
# One source of truth for the age rule: the same module the Modal worker imports.
COPY orbit_guard.py /opt/orbit_guard.py
COPY deploy/runpod/checkpoint.py /opt/checkpoint.py
COPY handler.py /opt/handler.py

WORKDIR /opt
CMD ["python", "-u", "/opt/handler.py"]
