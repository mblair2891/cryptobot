from __future__ import annotations

import os
from pathlib import Path


def on_vercel() -> bool:
    return bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))


def apply_vercel_demo_env(*, force: bool = False) -> None:
    """Force demo-only serverless settings. Live keys are ignored."""
    if not force and not on_vercel():
        return
    os.environ["MODE"] = "demo"
    os.environ["LIVE_CONFIRMED"] = "false"
    os.environ["AETHERGRID_EMBED_WORKER"] = "false"
    os.environ["AI_ENABLED"] = os.environ.get("AI_ENABLED") or "true"
    data_dir = "/tmp/aethergrid"
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = data_dir
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:////tmp/aethergrid-demo.db"
    os.environ["KILL_SWITCH_PATH"] = f"{data_dir}/KILL_SWITCH"
    os.environ["LOG_JSON"] = os.environ.get("LOG_JSON") or "true"
    # Ignore leftover live credentials (env wins over .env file).
    os.environ["COINBASE_API_KEY_NAME"] = ""
    os.environ["COINBASE_API_PRIVATE_KEY"] = ""
    os.environ["COINBASE_API_KEY_FILE"] = ""
