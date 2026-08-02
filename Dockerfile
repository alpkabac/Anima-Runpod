# syntax=docker/dockerfile:1.7

FROM runpod/worker-comfyui:5.8.5-base

# worker-comfyui ships an older comfy-cli that treats civitai.red as an unknown
# host and skips CIVITAI_API_TOKEN.  v1.7.3+ recognizes .red and attaches auth.
RUN pip install --no-cache-dir --upgrade "comfy-cli>=1.7.3"

# Megumin's workflow calls this repository-local node to turn the supplied
# roleplay scene into the final positive prompt.
COPY comfyui_custom_nodes/ComfyUI-Megumin-NanoGPT /comfyui/custom_nodes/ComfyUI-Megumin-NanoGPT
COPY data/anima_baked_loras.json /tmp/anima_baked_loras.json
COPY runpod/download_baked_loras.py /tmp/download_baked_loras.py

# Anima Turbo requires its Qwen text encoder and VAE in addition to the
# diffusion model.
RUN comfy model download \
  --url https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/diffusion_models/anima-turbo-v1.0.safetensors \
  --relative-path models/diffusion_models \
  --filename anima-turbo-v1.0.safetensors

RUN comfy model download \
  --url https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/diffusion_models/anima-base-v1.0.safetensors \
  --relative-path models/diffusion_models \
  --filename anima-base-v1.0.safetensors

RUN comfy model download \
  --url https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/text_encoders/qwen_3_06b_base.safetensors \
  --relative-path models/text_encoders \
  --filename qwen_3_06b_base.safetensors

RUN comfy model download \
  --url https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/vae/qwen_image_vae.safetensors \
  --relative-path models/vae \
  --filename qwen_image_vae.safetensors

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
