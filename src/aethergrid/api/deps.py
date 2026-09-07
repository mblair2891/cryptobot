from __future__ import annotations

from fastapi import Request

from aethergrid.runtime import AppRuntime


def get_runtime(request: Request) -> AppRuntime:
    return request.app.state.runtime
