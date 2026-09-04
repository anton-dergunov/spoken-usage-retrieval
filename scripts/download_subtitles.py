#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from speech_retrieval.acquisition import acquire


def main() -> None:
    parser = argparse.ArgumentParser(description="Cache Spanish YouTube captions without downloading video.")
    parser.add_argument("--limit", type=int, default=10, help="Total successful transcripts to retain")
    parser.add_argument("--scan-limit", type=int, default=25, help="Maximum candidates examined per channel")
    parser.add_argument("--channels", type=Path, default=Path("config/mvp_channels.json"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    report = acquire(
        config_path=args.channels,
        data_dir=args.data_dir,
        limit=args.limit,
        scan_limit=args.scan_limit,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["complete"]:
        raise SystemExit(f"Only acquired {report['successful']} of {report['requested']} transcripts")


if __name__ == "__main__":
    main()

