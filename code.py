from __future__ import annotations

import json
import os
from typing import Any, AsyncGenerator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .orchestrator import run_health_check_stream
from .schemas import HealthCheckRequest


app = FastAPI(
    title="SAP HANA Health Check Service",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _format_sse(event_name: str, data: Any) -> str:
    payload = json.dumps(data, default=_json_default, ensure_ascii=False)
    return f"event: {event_name}\ndata: {payload}\n\n"


def _normalize_event(raw_event: Any) -> tuple[str, Any]:
    if isinstance(raw_event, dict):
        event_name = (
            raw_event.get("event")
            or raw_event.get("event_name")
            or raw_event.get("type")
            or "pipeline_update"
        )
        data = raw_event.get("data", raw_event)
        return str(event_name), data

    event_name = (
        getattr(raw_event, "event_name", None)
        or getattr(raw_event, "event", None)
        or getattr(raw_event, "type", None)
        or "pipeline_update"
    )

    if hasattr(raw_event, "model_dump"):
        data = raw_event.model_dump()
    elif hasattr(raw_event, "__dict__"):
        data = vars(raw_event)
    else:
        data = {"message": str(raw_event)}

    return str(event_name), data


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "SAP HANA Health Check Service",
        "status": "running",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/sap-health-check/stream")
async def sap_health_check_stream(
    payload: HealthCheckRequest,
    request: Request,
) -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            yield _format_sse(
                "request_received",
                {
                    "message": "SAP Health Check request received.",
                    "gcs_bucket_path": payload.gcs_bucket_path,
                    "max_parallel_vms": payload.max_parallel_vms or 3,
                },
            )

            async for raw_event in run_health_check_stream(payload):
                if await request.is_disconnected():
                    break

                event_name, data = _normalize_event(raw_event)
                yield _format_sse(event_name, data)

        except Exception as exc:
            yield _format_sse(
                "pipeline_failed",
                {
                    "status": "failed",
                    "message": str(exc),
                },
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "status": "failed",
            "message": str(exc),
        },
    )


if __name__ == "__main__":
    uvicorn.run(
        "sap_hc_agent.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        reload=os.getenv("ENV", "").lower() == "local",
    )
