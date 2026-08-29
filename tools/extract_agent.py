#!/usr/bin/env python
"""Extract a competitor's agent from a public Kaggle notebook, verifiably.

    kaggle kernels pull <user>/<slug> -p opponents/<name>
    python tools/extract_agent.py opponents/<name>/*.ipynb --expect <sha256>

Why this exists
---------------

Our own strategies are the only opponents we can otherwise field, so a local
protocol grades us against ourselves. A public competitor agent grades us
against the meta — which is what the leaderboard actually measures.

Several strong public notebooks embed their whole agent as a compressed blob
(base85 over zlib) and publish its SHA-256 alongside. That makes extraction
*checkable*: we can prove we are sparring against the exact bytes the author
released, rather than something we reassembled and hoped was right. `--expect`
turns that into a hard failure rather than a footnote.

Boundaries, deliberately
------------------------

* Extracted agents live under `opponents/`, which is **gitignored**. They are not
  ours and not redistributable.
* Nothing under `agentlib/` may ever import them. They are only passed to
  `env.run()` as a file path, via the `file:<path>` opponent form.
* This runs third-party code locally when you spar against it. The checksum
  tells you *what* you are running, not that it is safe; only pull agents from
  public notebooks you are willing to execute.
"""

import argparse
import base64
import hashlib
import json
import re
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Long string literals are the only plausible payloads; anything shorter is a
#: variable name or a docstring fragment.
MIN_PAYLOAD = 2000


#: One quoted literal, no newline inside it.
_LITERAL = r"(?:'[^'\n]*'|\"[^\"\n]*\")"

#: A RUN of adjacent literals separated only by whitespace — Python's implicit
#: string concatenation. This is the form that actually appears in the wild: the
#: v48 notebook splits its 107 KB agent across ~1,200 literals of <=102 chars
#: each inside one parenthesised expression. Scanning for a single long literal
#: found nothing at all.
_RUN = re.compile(r"(?:" + _LITERAL + r"\s*){2,}|" + _LITERAL)


def _candidate_blobs(text: str):
    """Concatenated runs of adjacent string literals, longest first."""
    seen = set()
    for match in _RUN.finditer(text):
        joined = "".join(re.findall(_LITERAL, match.group(0)))
        # strip the quote characters from each fragment
        joined = "".join(
            frag[1:-1] for frag in re.findall(_LITERAL, match.group(0))
        )
        joined = "".join(joined.split())
        if len(joined) >= MIN_PAYLOAD and joined not in seen:
            seen.add(joined)
            yield joined


def _try_decode(blob: str) -> bytes | None:
    """Decode, decompress, then confirm it is really Python before believing it.

    Both axes vary in practice — notebooks use base85 or base64, and gzip or
    zlib — so try the cross product rather than assuming one combination.
    """
    import gzip

    decompressors = (zlib.decompress, gzip.decompress, lambda b: b)
    for decoder in (base64.b85decode, base64.b64decode, base64.a85decode):
        try:
            decoded = decoder(blob)
        except Exception:  # noqa: BLE001,S112 - not a payload; keep scanning
            continue
        for decompress in decompressors:
            try:
                raw = decompress(decoded)
            except Exception:  # noqa: BLE001,S112 - wrong codec; keep scanning
                continue
            try:
                compile(raw, "<candidate>", "exec")
            except (SyntaxError, ValueError):
                # ValueError, not just SyntaxError: `compile` raises it for
                # embedded null bytes, which is what a compressed *data* blob
                # looks like. Catching only SyntaxError crashed the tool.
                continue
            return raw
    return None


def extract(notebook: Path) -> list[bytes]:
    """All embedded, compilable Python payloads in a notebook, longest first."""
    text = notebook.read_text(errors="replace")
    # Notebook JSON escapes source lines; decoding to plain text first means the
    # payload appears as one contiguous literal rather than \n-split fragments.
    try:
        nb = json.loads(text)
        text = "\n".join(
            "".join(cell.get("source") or [])
            for cell in nb.get("cells", [])
        ) or text
    except Exception:  # noqa: BLE001,S110 - fall back to the raw file text
        pass

    found = []
    for blob in _candidate_blobs(text):
        raw = _try_decode(blob)
        if raw:
            found.append(raw)
    return sorted(found, key=len, reverse=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("notebook")
    ap.add_argument("--out", default=None,
                    help="where to write main.py (default: alongside the notebook)")
    ap.add_argument("--expect", default=None,
                    help="SHA-256 the notebook's author published; mismatch is fatal")
    args = ap.parse_args()

    nb = Path(args.notebook)
    if not nb.exists():
        print(f"no such notebook: {nb}")
        return 1

    payloads = extract(nb)
    if not payloads:
        print("No embedded agent found. Not every notebook packs one — some write")
        print("the agent inline with %%writefile, in which case just copy that file.")
        return 1

    agent = payloads[0]
    digest = hashlib.sha256(agent).hexdigest()
    print(f"found {len(payloads)} payload(s); largest is {len(agent):,} bytes")
    print(f"  sha256 {digest}")

    if args.expect:
        if digest != args.expect:
            print(f"  !! MISMATCH — author published {args.expect}")
            print("  !! Refusing to write. Benchmarking against bytes we cannot")
            print("  !! identify is worse than not benchmarking at all.")
            return 1
        print("  matches the published checksum")

    out = Path(args.out) if args.out else nb.parent / "main.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(agent)
    print(f"\nwrote {out}")
    try:
        rel = out.relative_to(ROOT)
    except ValueError:
        rel = out
    print(f"use as an opponent with:  file:{rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
