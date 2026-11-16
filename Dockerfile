# RunPod Serverless build entrypoint for the PUBLIC Krea 2 worker.
#
# This file exists at the repo root because a GitHub-connected RunPod build, and the RunPod
# Hub, look for `Dockerfile` there. It is intentionally identical to `deploy/runpod/Dockerfile`,
# which is what `deploy/runpod/publish.py` builds locally — keep the two in sync, and prefer
# this one when changing dependencies so the CI image and the local image cannot drift.
#
# The studio deck (uncensored merge, abliterated encoder, adapters, 4K, Wan video) is NOT in
# this image. This is the stock-core public line only.
#
# Ubuntu 24.04 has no CUDA 12.4 image (that series stops at ubuntu22.04), so 12.6.3 is the
# lowest base that keeps the distro's Python 3.12 without a third-party PPA. The version here
# matters for one reason: the base declares `NVIDIA_REQUIRE_CUDA=cuda>=12.6`, and that is what
# a host driver must satisfy to start this container at all. A 12.6 or 12.7 host used to fail
# at prestart for no functional reason. torch's cu128 wheels bring their own 12.8 runtime
# libraries and run on any 12.x driver under CUDA minor version compatibility (>= 525.60.13);
# SASS for Ampere/Ada is baked into the wheel, so PTX JIT on an older driver is not needed.
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HUB_ENABLE_HF_TRANSFER=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    PYTHONPATH=/opt:/opt/krea2 \
    # The base is a CUDA *runtime* image: no C compiler. `krea2/mmdit.py` decorates three
    # modules with @torch.compile, so the first forward hands a graph to Inductor and Triton's
    # driver builds a C extension with `cc` before it emits a kernel. `torch/_dynamo/config.py`
    # reads this name for `config.disable` (older releases called it TORCHDYNAMO_DISABLE).
    # `handler.pipeline()` also neutralises the decorator in-process, which a template's env
    # cannot undo.
    TORCH_COMPILE_DISABLE=1 \
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
COPY deploy/runpod/lowvram.py /opt/lowvram.py
COPY handler.py /opt/handler.py

WORKDIR /opt
CMD ["/opt/venv/bin/python", "-u", "/opt/handler.py"]
