# Anima RunPod

RunPod Serverless image for [Anima](https://huggingface.co/circlestone-labs/Anima)
generations, built on [`runpod/worker-comfyui`](https://github.com/runpod-workers/worker-comfyui)
(`5.10.0-base`, ComfyUI 0.34.0).

Everything is baked in at build time, so cold starts never download models:

| Path under `/comfyui/models` | Source |
|---|---|
| `diffusion_models/anima-turbo-v1.1.safetensors` | `circlestone-labs/Anima` |
| `text_encoders/qwen_3_06b_base.safetensors` | `circlestone-labs/Anima` |
| `vae/qwen_image_vae.safetensors` | `circlestone-labs/Anima` |
| `ipadapter/ip_adapter-Character_Reference-10.safetensors` | `LuciferTC/Anima-IP-Adapter` |
| `siglip2/siglip2-base-patch16-512/` | `google/siglip2-base-patch16-512` (SigLIP2 encoder used by the IP-Adapter) |
| `loras/*` | [`data/anima_baked_loras.json`](data/anima_baked_loras.json) |

Custom node: [`ComfyUI-Anima_IP-Adapter`](https://github.com/LuciferTC9527/ComfyUI-Anima_IP-Adapter),
pinned by commit in the `Dockerfile` (`AnimaIPAdapterLoader`, `AnimaIPAdapterApply`).

The image sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. A missing file
fails the job instead of silently downloading during a cold start.

## Building

`.github/workflows/docker-image.yml` builds and pushes
`ghcr.io/<owner>/anima-runpod:latest` and `:<sha>` on every push to `main` (or
manually through *Run workflow*). It needs the repository secret
`CIVITAI_API_TOKEN`, which is passed to the build as a BuildKit secret for the
baked LoRA downloads and is not stored in the image.

The base image ships PyTorch 2.11 built for CUDA 12.8. On the RunPod endpoint,
limit **Allowed CUDA versions** to 12.8 or newer.

## Using with Marinara Engine

This works with Marinara Engine's **RunPod Serverless (ComfyUI)** image
provider. The workflow runs text-to-image with Anima Turbo v1.1. When you give
it a character reference, it routes the model through the Anima IP-Adapter.

### Requirements

- Marinara Engine's `staging` branch, or any release **after 2.4.6**. Uploading
  `%reference_image_name%` files to RunPod came in
  [Pasta-Devs/Marinara-Engine#6700](https://github.com/Pasta-Devs/Marinara-Engine/pull/6700).
  2.4.6 and earlier can't fill that placeholder on RunPod.
- A RunPod Serverless endpoint running this image, and a RunPod API key.

### Set up the connection

1. In Marinara, open **Connections → Image Generation**, add a connection and
   pick **RunPod Serverless (ComfyUI)**.
2. Paste your RunPod **API key** and the endpoint's **RunPod Endpoint ID**. The ID
   is the part after `/v2/` in the endpoint URL, e.g. `abc123def456`.
3. Paste the whole contents of
   [`marinara/anima_ipadapter_runpod.workflow.txt`](marinara/anima_ipadapter_runpod.workflow.txt)
   into the workflow field.
4. **Ignore the "invalid JSON" warning** in the editor. The template has
   unquoted numeric placeholders (`"width": %width%`, `"seed": %seed%`, …).
   Marinara replaces them as plain text before it parses the JSON, so the
   request that reaches RunPod is valid JSON with real numbers. Quoting them
   would send strings, which ComfyUI rejects. The file is `.txt` for the same
   reason.
5. Turn on **"Upload a 1x1 placeholder when no reference image is provided"**.
   Without it, a generation with no character reference sends the literal text
   `%reference_image_name%` to `LoadImage`, and the job fails.

### How the reference switch works

`LoadImage` always loads *something*: the real reference, or Marinara's
placeholder. `GetImageSize` → `ComfyMathExpression` (`a > 16 and b > 16`) →
`ComfySwitchNode` then picks the model:

- **Reference larger than 16×16:** the model goes through `AnimaIPAdapterApply`
  (character reference).
- **Placeholder:** the plain Anima model is used. The IP-Adapter branch is lazy,
  so it isn't evaluated at all.

The threshold is 16 rather than 1 because Marinara's placeholder is a 16×16 PNG,
despite the "1x1" label.

`AnimaIPAdapterLoader` keeps `auto_download: true`. Because the SigLIP2 encoder
is baked into the directory the node checks, this never touches the network.
Setting it to `false` behaves the same in this image.

### Size limit

RunPod rejects `/run` requests over **10 MiB**, and Marinara checks this before
sending. The reference image is sent base64-encoded (about 4/3 of the file
size) inside that limit, so keep references under roughly 7 MB. A 512–1024 px
PNG or JPEG is plenty: the IP-Adapter resizes it to 512 px anyway.

### Testing without Marinara

`scripts/test_payload.py` builds the same payload Marinara sends (prompt
`1girl, smile`, 832×1216, seed 123 by default). It uses only the Python
standard library:

```bash
# print the payload (base64 shortened) with a character reference
python3 scripts/test_payload.py --ref character.png

# no reference: 1x1 placeholder, or Marinara's actual 16x16 placeholder
python3 scripts/test_payload.py --placeholder 1x1
python3 scripts/test_payload.py --placeholder marinara

# send it to RunPod and save the result
export RUNPOD_API_KEY=...        # never commit this
export RUNPOD_ENDPOINT_ID=...
python3 scripts/test_payload.py --ref character.png --save with_ref.png
python3 scripts/test_payload.py --placeholder marinara --save no_ref.png
```

When both environment variables are set, the script POSTs to
`/v2/<endpoint>/run`, polls `/status`, and writes `output.images[0].data` to
`--save`. Add `--no-send` to only print. Add `--out payload.json` to write the
full payload to a file.
