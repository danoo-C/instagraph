"""CLI: ``python -m instagraph.sample --messages 2000000 --out <dir> [--seed N] [--zip]``."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from instagraph.sample.generator import generate_export, zip_export


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m instagraph.sample")
    ap.add_argument("--messages", type=int, default=20_000)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--layout", choices=["new", "old"], default="new")
    ap.add_argument("--owner", default="Daniel")
    ap.add_argument("--media", action="store_true", help="write a few placeholder JPEGs")
    ap.add_argument("--zip", action="store_true", help="also write <out>.zip")
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    root = generate_export(
        a.out,
        messages=a.messages,
        seed=a.seed,
        layout=a.layout,
        owner=a.owner,
        with_media_files=a.media,
    )
    print(f"export written to {root} in {time.perf_counter() - t0:.1f}s")
    if a.zip:
        z = zip_export(root, root.with_suffix(".zip"))
        print(f"zip written to {z}")


if __name__ == "__main__":
    main()
