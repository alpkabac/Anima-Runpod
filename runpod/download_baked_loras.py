"""Download the user-maintained Anima baked-LoRA manifest during Docker build."""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(manifest, list):
    raise ValueError("Anima baked LoRA manifest must be a JSON array")

civitai_token = os.environ.get("CIVITAI_API_TOKEN", "").strip()

for item in manifest:
    if not isinstance(item, dict):
        raise ValueError("Every Anima baked LoRA item must be an object")
    filename = str(item.get("filename", "")).strip()
    url = str(item.get("url", "")).strip()
    if not filename.lower().endswith(".safetensors") or "/" in filename or "\\" in filename:
        raise ValueError(f"Invalid baked LoRA filename: {filename!r}")
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"Invalid baked LoRA URL for {filename!r}")
    host = (urlparse(url).hostname or "").lower()
    if host in {"civitai.com", "civitai.red"} or host.endswith((".civitai.com", ".civitai.red")):
        if not civitai_token:
            raise ValueError(
                "CIVITAI_API_TOKEN is required to download baked Civitai LoRAs "
                f"(missing for {filename!r})"
            )
    subprocess.run(
        ["comfy", "model", "download", "--url", url, "--relative-path", "models/loras", "--filename", filename],
        check=True,
    )
