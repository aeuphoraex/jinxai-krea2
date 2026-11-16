# RunPod serverless worker for the PUBLIC Krea 2 line.
#
# Same image as the root `Dockerfile`; this copy is the one `deploy/runpod/publish.py` builds
# when a local Docker daemon is available. Keep the two identical.
#
# Built from the CUDA runtime image rather than a guessed RunPod PyTorch tag: the tag names in
# registry.runpod.ai change, and a wrong tag fails at `docker build` on the user's machine, not
# at launch time. Python 3.12 + torch cu128 is what krea2/pyproject.toml asks for.
#
# 12.6.3, not 12.8.1: Ubuntu 24.04 has no CUDA 12.4 image, and 12.6 is the first series with a
# `cudnn-runtime-ubuntu24.04` tag, so it is the lowest floor reachable while keeping the
# distro's Python 3.12. `NVIDIA_REQUIRE_CUDA=cuda>=12.6` is the gate a host driver has to pass
# to start the container, so this line decides whether 12.6/12.7 hosts can run us at all.
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

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
# pip` dies with "Cannot uninstall pip 24.0". Both problems vanish inside a venv. `click`
# specifically is imported at the top of krea2/inference.py, and torchvision is what
# transformers' AutoProcessor reaches for while building the Qwen3-VL processor.
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/python -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cu128 torch torchvision \
    && /opt/venv/bin/python -m pip install --no-cache-dir \
        transformers==4.57.1 safetensors>=0.4.0 pillow>=10.0.0 einops>=0.7.0 \
        diffusers>=0.32.0 accelerate>=1.0.0 sentencepiece click hf_transfer runpod

# The package this deployment already uses; the weights stay on the network volume.
COPY krea2/ /opt/krea2/
# One source of truth for the age rule: the same module the Modal worker imports.
COPY orbit_guard.py /opt/orbit_guard.py
COPY deploy/runpod/checkpoint.py /opt/checkpoint.py
COPY handler.py /opt/handler.py

WORKDIR /opt
CMD ["/opt/venv/bin/python", "-u", "/opt/handler.py"]
