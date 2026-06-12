"""
Minimal .env loader for the ui-replicator skill.

Usage:
    from load_keys import load_keys
    load_keys()  # raises FileNotFoundError if .env is missing
    import os
    api_key = os.environ["GEMINI_API_KEY"]

Reads the .env that sits next to this skill folder (one level above scripts/).
Lines look like:  KEY=value   (quotes optional, # for comments)
Existing env vars take priority over .env (so CI overrides work).
"""
import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


PLACEHOLDER_MARKERS = ("PASTE_YOUR_", "AIza...", "这里填写", "填写Key")


def load_keys(path: Path | None = None) -> dict[str, str]:
    p = Path(path) if path else ENV_PATH
    if not p.exists():
        raise FileNotFoundError(
            f".env not found at {p}\n"
            f"There should be a .env file shipped with this skill — open it and fill in your keys."
        )
    loaded, placeholders = {}, []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if not k or not v:
            continue
        if any(m in v for m in PLACEHOLDER_MARKERS):
            placeholders.append(k)
            continue
        if k in os.environ:
            loaded[k] = "skipped (already set)"
            continue
        os.environ[k] = v
        loaded[k] = "loaded"
    if placeholders:
        raise ValueError(
            f"These keys in {p} still have placeholder values: {', '.join(placeholders)}.\n"
            f"Open the .env file and replace the PASTE_YOUR_..._HERE values with real keys."
        )
    return loaded


if __name__ == "__main__":
    s = load_keys()
    for k, status in s.items():
        n = len(os.environ.get(k, ""))
        print(f"{k:<24s} {status:<22s} ({n} chars)")
