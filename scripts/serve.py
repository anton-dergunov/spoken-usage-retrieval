#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from speech_retrieval.api import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the speech retrieval API and web viewer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--web-dist", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    if not (args.web_dist / "index.html").exists():
        raise SystemExit("Frontend build missing. Run: npm --prefix web run build")
    app = create_app(data_dir=args.data_dir, web_dist=args.web_dist)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
