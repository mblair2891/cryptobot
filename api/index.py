"""Vercel Python entrypoint — DEMO UI only.

Real paper/live trading is Docker / Railway / Render, not this function.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aethergrid.vercel_env import apply_vercel_demo_env  # noqa: E402

apply_vercel_demo_env(force=True)

from aethergrid.config import reset_settings  # noqa: E402

reset_settings()

from aethergrid.main import app  # noqa: E402

try:
    from mangum import Mangum

    handler = Mangum(app, lifespan="auto")
except ImportError:  # pragma: no cover - native Vercel ASGI uses `app`
    handler = app
