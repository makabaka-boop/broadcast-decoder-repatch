"""FastAPI application for decode-port baseline assignment and fault
re-arrangement.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import db, services
from .validation import ValidationError


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_pool()
    try:
        yield
    finally:
        db.close_pool()


app = FastAPI(title="Decoded Signal Port Planner", version="1.0.0",
              lifespan=lifespan)


@app.exception_handler(ValidationError)
async def _validation_handler(_request: Request, exc: ValidationError):
    return JSONResponse(status_code=422, content={"detail": exc.message})


@app.exception_handler(services.NotFound)
async def _not_found_handler(_request: Request, _exc: services.NotFound):
    return JSONResponse(status_code=404, content={"detail": "not found"})


async def _read_json(request: Request):
    body = await request.body()
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValidationError("request body must be valid JSON")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/snapshots")
async def create_snapshot(request: Request):
    payload = await _read_json(request)
    return await run_in_threadpool(services.create_snapshot, payload)


@app.get("/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    return await run_in_threadpool(services.get_snapshot, snapshot_id)


@app.post("/snapshots/{snapshot_id}/baselines")
async def create_baseline(snapshot_id: str):
    return await run_in_threadpool(services.create_baseline, snapshot_id)


@app.get("/baselines/{baseline_id}")
async def get_baseline(baseline_id: str):
    return await run_in_threadpool(services.get_baseline, baseline_id)


@app.post("/baselines/{baseline_id}/rearrangements")
async def create_rearrangement(baseline_id: str, request: Request):
    payload = await _read_json(request)
    return await run_in_threadpool(
        services.create_rearrangement, baseline_id, payload)


@app.get("/rearrangements/{rearrangement_id}")
async def get_rearrangement(rearrangement_id: str):
    return await run_in_threadpool(services.get_rearrangement, rearrangement_id)
