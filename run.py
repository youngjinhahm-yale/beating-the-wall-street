"""One command to start Beating the Wall Street: python run.py

Starts the FastAPI backend (which also serves the built website and launches the
market MCP server when an agent first needs it) and opens your browser.
"""

from __future__ import annotations

import sys as _sys

for _s in (_sys.stdout, _sys.stderr):  # Windows consoles may not be UTF-8
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    import uvicorn

    from backend import config
    from backend.db import init_db

    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if args.no_browser:
        os.environ["NO_BROWSER"] = "1"

    init_db()
    port = args.port
    host = os.getenv("HOST", "127.0.0.1")
    url = f"http://localhost:{port}"
    key = "found" if (os.getenv("PORTKEY_API_KEY") or os.getenv("OPENAI_API_KEY")) else "MISSING (AI features off)"
    print(f"\n  Beating the Wall Street\n  Data mode: {config.DATA_MODE} ({config.DB_PATH.name})\n"
          f"  API key:   {key}\n  Open:      {url}\n")
    if not (config.FRONTEND_DIST / "index.html").exists():
        print("  Note: frontend/dist is missing. Build it with: cd frontend && npm install && npm run build\n")
    if os.getenv("NO_BROWSER") != "1":
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    uvicorn.run("backend.main:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
