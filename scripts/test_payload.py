#!/usr/bin/env python3
"""Build (and optionally send) the RunPod payload Marinara Engine would send.

Mirrors packages/server/src/services/image/runpod-comfyui.service.ts in
Pasta-Devs/Marinara-Engine: placeholders are replaced as plain text before the
template is parsed, text values are JSON-escaped, numeric values are inserted
raw, and %reference_image_name% becomes a filename uploaded via input.images.

Examples:
  # print the payload with a local character reference
  python3 scripts/test_payload.py --ref path/to/character.png

  # no reference: upload a 1x1 placeholder (should take the plain-model branch)
  python3 scripts/test_payload.py --placeholder 1x1

  # same, but with Marinara's real 16x16 placeholder image
  python3 scripts/test_payload.py --placeholder marinara

  # send it: set RUNPOD_API_KEY and RUNPOD_ENDPOINT_ID, result saved to --save
  RUNPOD_API_KEY=... RUNPOD_ENDPOINT_ID=... \\
    python3 scripts/test_payload.py --ref character.png --save out.png

Uses only the Python standard library.
"""

import argparse
import base64
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = REPO_ROOT / "marinara" / "anima_ipadapter_runpod.workflow.txt"
RUNPOD_API = "https://api.runpod.ai/v2"
REQUEST_LIMIT_BYTES = 10 * 1024 * 1024  # RunPod /run body limit (10 MiB)
REFERENCE_NAME = "ref.png"

# COMFYUI_PLACEHOLDER_REFERENCE_BASE64 from
# packages/shared/src/constants/image-generation-defaults.ts (a 16x16 RGBA PNG).
MARINARA_PLACEHOLDER_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQ"
    "AAAAZdEVYdFNvZnR3YXJlAFBhaW50Lk5FVCA1LjEuMTITAUd0AAAAuGVYSWZJSSoACAAAAAUAGgEFAAEAAABKAAAAGwEFAAEAAABSAAAAKAEDAAEAAAA"
    "CAAAAMQECABEAAABaAAAAaYcEAAEAAABsAAAAAAAAAGAAAAABAAAAYAAAAAEAAABQYWludC5ORVQgNS4xLjEyAAADAACQBwAEAAAAMDIzMAGhAwABAAA"
    "AAQAAAAWgBAABAAAAlgAAAAAAAAACAAEAAgAEAAAAUjk4AAIABwAEAAAAMDEwMAAAAADZp5qVybcLXwAAABJJREFUOE9jYBgFo2AUjAIIAAAEEAABTLt"
    "GVQAAAABJRU5ErkJggg=="
)


def escape_json_str(value):
    """Same escaping as Marinara's escapeJsonStr()."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def solid_png(width, height, rgba=(0, 0, 0, 0)):
    """Encode a solid-color RGBA PNG without Pillow."""

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    row = b"\x00" + bytes(rgba) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def build_payload(template_text, *, prompt, negative_prompt, width, height, seed, steps, cfg, denoise, reference_b64):
    wf = template_text
    replacements = {
        "%prompt%": escape_json_str(prompt),
        "%negative_prompt%": escape_json_str(negative_prompt),
        "%width%": str(width),
        "%height%": str(height),
        "%seed%": str(seed),
        "%steps%": str(steps),
        "%cfg%": str(cfg),
        "%cfg_scale%": str(cfg),
        "%denoise%": str(denoise),
    }
    for placeholder, value in replacements.items():
        wf = wf.replace(placeholder, value)

    images = []
    if reference_b64 is not None:
        for placeholder in ("%reference_image_name%", "%reference_image_name_01%"):
            if placeholder in wf:
                wf = wf.replace(placeholder, REFERENCE_NAME)
                if not images:
                    images.append({"name": REFERENCE_NAME, "image": reference_b64})
        wf = wf.replace("%reference_image%", escape_json_str(reference_b64))

    leftover = sorted({tok for tok in wf.split("%")[1::2] if tok and tok.replace("_", "").isalnum()})
    if leftover:
        print(f"warning: unreplaced placeholders: {', '.join('%' + t + '%' for t in leftover)}", file=sys.stderr)

    try:
        workflow = json.loads(wf)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Template is not valid JSON after substitution: {exc}")

    payload = {"input": {"workflow": workflow}}
    if images:
        payload["input"]["images"] = images
    return payload


def redacted(payload):
    """Copy of the payload with base64 image data shortened for printing."""
    clone = json.loads(json.dumps(payload))
    for image in clone["input"].get("images", []):
        data = image["image"]
        if len(data) > 80:
            image["image"] = f"{data[:40]}...<{len(data)} base64 chars>"
    return clone


def runpod_request(method, url, api_key, body=None):
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"RunPod {method} {url} failed: HTTP {exc.code}: {exc.read().decode(errors='replace')[:500]}")


def send(payload_bytes, api_key, endpoint_id, save_path, timeout_s):
    base = f"{RUNPOD_API}/{endpoint_id}"
    job = runpod_request("POST", f"{base}/run", api_key, payload_bytes)
    job_id = job.get("id")
    if not job_id:
        raise SystemExit(f"RunPod did not return a job id: {job}")
    print(f"submitted job {job_id}", file=sys.stderr)

    started = time.monotonic()
    while True:
        status = runpod_request("GET", f"{base}/status/{job_id}", api_key)
        state = status.get("status")
        if state == "COMPLETED":
            break
        if state in {"FAILED", "CANCELLED", "TIMED_OUT"}:
            raise SystemExit(f"job {state}: {json.dumps(status)[:2000]}")
        if time.monotonic() - started > timeout_s:
            raise SystemExit(f"gave up after {timeout_s}s (last status {state})")
        time.sleep(2)

    print(
        f"completed in {time.monotonic() - started:.1f}s "
        f"(delayTime={status.get('delayTime')}ms executionTime={status.get('executionTime')}ms)",
        file=sys.stderr,
    )
    images = (status.get("output") or {}).get("images") or []
    if not images or not images[0].get("data"):
        raise SystemExit(f"no output.images[0].data in response: {json.dumps(status)[:2000]}")
    data = images[0]["data"]
    if data.startswith("data:"):
        data = data.split(",", 1)[1]
    image_bytes = base64.b64decode(data)
    Path(save_path).write_bytes(image_bytes)
    print(f"saved {save_path} ({len(image_bytes)} bytes, size={png_size(image_bytes)})", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    ref = parser.add_mutually_exclusive_group()
    ref.add_argument("--ref", type=Path, help="local reference image, uploaded as ref.png")
    ref.add_argument(
        "--placeholder",
        choices=["1x1", "marinara"],
        help="upload a placeholder instead of a reference: a 1x1 PNG, or Marinara's 16x16 placeholder",
    )
    parser.add_argument("--prompt", default="1girl, smile")
    parser.add_argument("--negative-prompt", default="")
    parser.add_argument("--width", type=int, default=832)
    parser.add_argument("--height", type=int, default=1216)
    parser.add_argument("--seed", type=int, default=123)
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--cfg", type=float, default=1)
    parser.add_argument("--denoise", type=float, default=1)
    parser.add_argument("--out", type=Path, help="write the full payload JSON to this file")
    parser.add_argument("--full", action="store_true", help="print base64 image data in full")
    parser.add_argument("--save", default="runpod_result.png", help="where to save the generated image")
    parser.add_argument("--timeout", type=int, default=600, help="seconds to wait for the RunPod job")
    parser.add_argument("--no-send", action="store_true", help="never POST, even if RunPod env vars are set")
    args = parser.parse_args()

    reference_b64 = None
    if args.ref:
        reference_b64 = base64.b64encode(args.ref.read_bytes()).decode("ascii")
    elif args.placeholder == "1x1":
        reference_b64 = base64.b64encode(solid_png(1, 1)).decode("ascii")
    elif args.placeholder == "marinara":
        reference_b64 = MARINARA_PLACEHOLDER_B64
    if reference_b64 is not None:
        size = png_size(base64.b64decode(reference_b64))
        print(f"reference: {size[0]}x{size[1]} PNG" if size else "reference: non-PNG image", file=sys.stderr)
    elif "%reference_image_name" in args.template.read_text(encoding="utf-8"):
        print(
            "warning: template uses %reference_image_name% but no --ref/--placeholder was given; "
            "LoadImage will fail on RunPod",
            file=sys.stderr,
        )

    payload = build_payload(
        args.template.read_text(encoding="utf-8"),
        prompt=args.prompt,
        negative_prompt=args.negative_prompt,
        width=args.width,
        height=args.height,
        seed=args.seed,
        steps=args.steps,
        cfg=args.cfg,
        denoise=args.denoise,
        reference_b64=reference_b64,
    )
    payload_bytes = json.dumps(payload).encode("utf-8")
    print(f"payload: {len(payload_bytes)} bytes (limit {REQUEST_LIMIT_BYTES})", file=sys.stderr)
    if len(payload_bytes) > REQUEST_LIMIT_BYTES:
        raise SystemExit("payload exceeds RunPod's 10 MiB request limit; use a smaller reference image")

    if args.out:
        args.out.write_bytes(payload_bytes)
        print(f"wrote {args.out}", file=sys.stderr)

    api_key = os.environ.get("RUNPOD_API_KEY", "").strip()
    endpoint_id = os.environ.get("RUNPOD_ENDPOINT_ID", "").strip()
    if api_key and endpoint_id and not args.no_send:
        send(payload_bytes, api_key, endpoint_id, args.save, args.timeout)
    else:
        print(json.dumps(payload if args.full else redacted(payload), indent=2))


if __name__ == "__main__":
    main()
