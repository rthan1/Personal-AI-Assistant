import hmac
import threading
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from assistant.bridge.service import ToolService


class ToolCall(BaseModel):
    sender: str
    args: dict[str, Any] = Field(default_factory=dict)


class ContextRequest(BaseModel):
    sender: str


def create_bridge_app(service: ToolService, bridge_token: str) -> FastAPI:
    """Local-only API the Hermes plugin calls. Never expose this port through the tunnel."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    lock = threading.Lock()

    def check_token(authorization: str) -> None:
        if not hmac.compare_digest(authorization, f"Bearer {bridge_token}"):
            raise HTTPException(status_code=401)

    @app.post("/tools/{tool}")
    def call_tool(tool: str, call: ToolCall, authorization: str = Header(default="")) -> dict:
        check_token(authorization)
        with lock:
            return service.call(tool, call.sender, call.args)

    @app.post("/context")
    def context(request: ContextRequest, authorization: str = Header(default="")) -> dict:
        check_token(authorization)
        with lock:
            return service.context(request.sender)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    return app
