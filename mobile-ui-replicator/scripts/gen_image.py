#!/usr/bin/env python3
"""
Generate a single image with Gemini 2.5 Flash Image, conditioned on a style reference.

Usage:
    python scripts/gen_image.py \
        --prompt "...the painted asset description..." \
        --style ./style-ref.jpg \
        --aspect 3:4 \
        --out ./output/<name>/assets/hero.png \
        [--style-preamble ./prompts/style-preamble-filled.txt] \
        [--retries 2]

The agent typically:
  1. Reads prompts/style-extraction.md, looks at the user's style image, writes a STYLE preamble to a temp file.
  2. For each asset in assets.json, calls this script with --prompt (just the per-asset body),
     --style-preamble (the preamble file), and --aspect (the aspect ratio from assets.json).
  3. The script concatenates preamble + prompt and sends them to Gemini together with the
     inlined style image AND the aspect ratio constraint.

Without --aspect, the output dimensions follow the style reference's shape, which is usually wrong.

Exits non-zero on failure with a message on stderr.
"""
import argparse, base64, json, os, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_keys import load_keys  # noqa: E402


MODEL = "gemini-2.5-flash-image"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# Aspect ratios accepted by gemini-2.5-flash-image's imageConfig.aspectRatio.
# Anything else gets rejected by the API — we validate up front so the user gets a clean error.
SUPPORTED_ASPECTS = {"1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2", "4:5", "5:4", "21:9"}


def gen_image(prompt: str, ref_b64: str, ref_mime: str, api_key: str,
              aspect: str | None = None, retries: int = 2):
    generation_config: dict = {"responseModalities": ["IMAGE", "TEXT"]}
    if aspect:
        generation_config["imageConfig"] = {"aspectRatio": aspect}
    payload = {
        "contents": [{
            "parts": [
                {"inlineData": {"mimeType": ref_mime, "data": ref_b64}},
                {"text": prompt},
            ]
        }],
        "generationConfig": generation_config,
    }
    url = f"{ENDPOINT}?key={api_key}"
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=180)
            data = json.loads(resp.read())
            for p in data["candidates"][0]["content"]["parts"]:
                if "inlineData" in p:
                    return base64.b64decode(p["inlineData"]["data"]), None
            last_err = "no inlineData in response"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(3 + attempt * 2)
    return None, last_err


def mime_for(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp",
    }.get(ext, "image/jpeg")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate one image with Gemini, style-conditioned.")
    ap.add_argument("--prompt", required=True, help="Per-asset description. Concatenated AFTER --style-preamble.")
    ap.add_argument("--style", required=True, help="Path to the style reference image (jpg/png/webp).")
    ap.add_argument("--out", required=True, help="Output PNG path.")
    ap.add_argument("--aspect", help=f"Output aspect ratio. One of: {', '.join(sorted(SUPPORTED_ASPECTS))}. "
                                     "Strongly recommended — without it Gemini follows the style ref's shape.")
    ap.add_argument("--style-preamble", help="Optional path to a text file with the STYLE TRANSFER block.")
    ap.add_argument("--retries", type=int, default=2)
    args = ap.parse_args()

    if args.aspect and args.aspect not in SUPPORTED_ASPECTS:
        print(f"ERROR: --aspect {args.aspect!r} not supported. Use one of: "
              f"{', '.join(sorted(SUPPORTED_ASPECTS))}", file=sys.stderr)
        return 2

    load_keys()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set after load_keys() — check .env", file=sys.stderr)
        return 2

    style_path = Path(args.style)
    if not style_path.exists():
        print(f"ERROR: style image not found: {style_path}", file=sys.stderr)
        return 2

    full_prompt = args.prompt
    if args.style_preamble:
        pre = Path(args.style_preamble)
        if not pre.exists():
            print(f"ERROR: style-preamble file not found: {pre}", file=sys.stderr)
            return 2
        full_prompt = pre.read_text(encoding="utf-8").strip() + "\n\n" + args.prompt

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ref_b64 = base64.b64encode(style_path.read_bytes()).decode()
    t0 = time.time()
    img, err = gen_image(full_prompt, ref_b64, mime_for(style_path), api_key,
                         aspect=args.aspect, retries=args.retries)
    dt = time.time() - t0
    if not img:
        print(f"FAILED ({dt:.1f}s): {err}", file=sys.stderr)
        return 1
    out_path.write_bytes(img)
    aspect_note = f" [{args.aspect}]" if args.aspect else " [no aspect set]"
    print(f"OK  {out_path}  {len(img)//1024} KB{aspect_note}  ({dt:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
