#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from speech_retrieval.indexing import build_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the local exact word and phrase index.")
    parser.add_argument("--max-ngram", type=int, default=5)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    report = build_index(data_dir=args.data_dir, max_ngram=args.max_ngram)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

