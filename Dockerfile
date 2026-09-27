# syntax=docker/dockerfile:1.7

# 5.10.0-base installs ComfyUI 0.34.0.  The Marinara workflow needs
# ComfyMathExpression's BOOL output (added in ComfyUI 0.21.0); 5.8.5-base was
# built with ComfyUI 0.17.2, which only has FLOAT/INT outputs.
FROM runpod/worker-comfyui:5.10.0-base

# Older worker-comfyui images ship a comfy-cli that treats civitai.red as an
# unknown host and skips CIVITAI_API_TOKEN.  v1.7.3+ recognizes .red and
# attaches auth.  5.10.0-base pins comfy-cli 1.13.0, so this is a guard, not an
# upgrade: it keeps the base image's pinned version when it already qualifies.
RUN pip install --no-cache-dir "comfy-cli>=1.7.3"

COPY data/anima_baked_loras.json /tmp/anima_baked_loras.json
COPY runpod/download_baked_loras.py /tmp/download_baked_loras.py

# Anima IP-Adapter (character reference) used by the Marinara workflow.
# Pinned commit; bump deliberately after checking node/input names still match
# marinara/anima_ipadapter_runpod.workflow.txt.
ARG ANIMA_IPADAPTER_REPO=https://github.com/LuciferTC9527/ComfyUI-Anima_IP-Adapter.git
ARG ANIMA_IPADAPTER_COMMIT=6b77cd0c367d76402174ace2be50d3cb6aa77855
RUN git clone "$ANIMA_IPADAPTER_REPO" /comfyui/custom_nodes/ComfyUI-Anima_IP-Adapter \
    && git -C /comfyui/custom_nodes/ComfyUI-Anima_IP-Adapter checkout --detach "$ANIMA_IPADAPTER_COMMIT" \
    && rm -rf /comfyui/custom_nodes/ComfyUI-Anima_IP-Adapter/.git \
    && pip install --no-cache-dir -r /comfyui/custom_nodes/ComfyUI-Anima_IP-Adapter/requirements.txt

# Anima Turbo requires its Qwen text encoder and VAE in addition to the
# diffusion model.
RUN comfy model download \
  --url https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/diffusion_models/anima-turbo-v1.1.safetensors \
  --relative-path models/diffusion_models \
  --filename anima-turbo-v1.1.safetensors

RUN comfy model download \
  --url https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/text_encoders/qwen_3_06b_base.safetensors \
  --relative-path models/text_encoders \
  --filename qwen_3_06b_base.safetensors

RUN comfy model download \
  --url https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/vae/qwen_image_vae.safetensors \
  --relative-path models/vae \
  --filename qwen_image_vae.safetensors

# IP-Adapter weights.  AnimaIPAdapterLoader lists models/ipadapter/ and never
# downloads the adapter itself.
RUN comfy model download \
  --url https://huggingface.co/LuciferTC/Anima-IP-Adapter/resolve/main/ip_adapter-Character_Reference-10.safetensors \
  --relative-path models/ipadapter \
  --filename ip_adapter-Character_Reference-10.safetensors

# SigLIP2 vision encoder.  With auto_download=true the loader calls
# snapshot_download(google/siglip2-base-patch16-512) into this exact directory
# only when config.json is missing; otherwise (and with auto_download=false) it
# loads it with from_pretrained(<dir>) and makes no network calls.  Baking it
# here keeps cold starts offline.
RUN python -c "from huggingface_hub import snapshot_download; \
snapshot_download(repo_id='google/siglip2-base-patch16-512', \
local_dir='/comfyui/models/siglip2/siglip2-base-patch16-512', \
allow_patterns=['*.json', '*.safetensors', '*.model', '*.txt'])" \
    && test -f /comfyui/models/siglip2/siglip2-base-patch16-512/config.json \
    && ls /comfyui/models/siglip2/siglip2-base-patch16-512/*.safetensors

# Add Civitai or Hugging Face LoRAs to data/anima_baked_loras.json.  Keep NSFW
# assets on civitai.red.  CIVITAI_API_TOKEN is a BuildKit secret only for this
# step; it is not copied into the final image.
RUN --mount=type=secret,id=civitai_api_token \
    CIVITAI_API_TOKEN="$(cat /run/secrets/civitai_api_token 2>/dev/null || true)" \
    && if [ -z "$CIVITAI_API_TOKEN" ]; then \
         echo "Missing BuildKit secret civitai_api_token (set GitHub secret CIVITAI_API_TOKEN)." >&2; \
         exit 1; \
       fi \
    && export CIVITAI_API_TOKEN \
    && python3 /tmp/download_baked_loras.py /tmp/anima_baked_loras.json

# Everything the workflows need is baked in above.  Fail fast instead of
# silently downloading from Hugging Face during a serverless cold start.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
