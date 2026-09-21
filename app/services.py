"""Service operations: persistence (single transaction per mutation) combined
with planning.  Functions are synchronous and CPU/DB bound; the HTTP layer
runs them in worker threads.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from . import db
from .planner import plan_baseline, plan_rearrangement
from .validation import ValidationError, validate_failed_ports, validate_snapshot


class NotFound(Exception):
    pass


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise NotFound()


def _iso(row) -> str:
    return row["created_at"].isoformat()


def create_snapshot(raw_payload: Any) -> Dict[str, Any]:
    snapshot = validate_snapshot(raw_payload)
    payload = {
        "signals": [{"id": sid, "importance": imp}
                    for sid, imp in snapshot["signals"]],
        "ports": [{"id": pid} for pid in snapshot["ports"]],
        "edges": [{"signal_id": sid, "port_id": pid, "quality": q}
                  for sid, pid, q in snapshot["edges"]],
    }
    with db.connection() as conn:
        row = conn.execute(
            "INSERT INTO snapshots (payload) VALUES (%s) "
            "RETURNING id, created_at",
            (payload,),
        ).fetchone()
        conn.commit()
    snapshot_id = row["id"]
    return {
        "id": str(snapshot_id),
        "created_at": _iso(row),
        "signals": payload["signals"],
        "ports": payload["ports"],
        "edges": payload["edges"],
    }


def _get_snapshot_row(conn, snapshot_id: uuid.UUID):
    row = conn.execute(
        "SELECT id, payload, created_at FROM snapshots WHERE id = %s",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        raise NotFound()
    return row


def _snapshot_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "signals": [(item["id"], item["importance"])
                    for item in payload["signals"]],
        "ports": [item["id"] for item in payload["ports"]],
        "edges": [(item["signal_id"], item["port_id"], item["quality"])
                  for item in payload["edges"]],
    }


def get_snapshot(snapshot_id_text: str) -> Dict[str, Any]:
    snapshot_id = _parse_uuid(snapshot_id_text)
    with db.connection() as conn:
        row = _get_snapshot_row(conn, snapshot_id)
    payload = row["payload"]
    return {
        "id": str(row["id"]),
        "created_at": _iso(row),
        "signals": payload["signals"],
        "ports": payload["ports"],
        "edges": payload["edges"],
    }


def create_baseline(snapshot_id_text: str) -> Dict[str, Any]:
    snapshot_id = _parse_uuid(snapshot_id_text)
    with db.connection() as conn:
        row = _get_snapshot_row(conn, snapshot_id)
        snapshot = _snapshot_from_payload(row["payload"])
        result = plan_baseline(snapshot)
        saved = conn.execute(
            "INSERT INTO baselines "
            "(snapshot_id, importance_sum, quality_sum, pairs) "
            "VALUES (%s, %s, %s, %s) "
            "RETURNING id, created_at",
            (snapshot_id, result["importance_sum"], result["quality_sum"],
             result["pairs"]),
        ).fetchone()
        conn.commit()
    return {
        "id": str(saved["id"]),
        "snapshot_id": str(snapshot_id),
        "created_at": _iso(saved),
        "importance_sum": result["importance_sum"],
        "quality_sum": result["quality_sum"],
        "pairs": result["pairs"],
    }


def get_baseline(baseline_id_text: str) -> Dict[str, Any]:
    baseline_id = _parse_uuid(baseline_id_text)
    with db.connection() as conn:
        row = conn.execute(
            "SELECT id, snapshot_id, importance_sum, quality_sum, pairs, "
            "created_at FROM baselines WHERE id = %s",
            (baseline_id,),
        ).fetchone()
    if row is None:
        raise NotFound()
    return {
        "id": str(row["id"]),
        "snapshot_id": str(row["snapshot_id"]),
        "created_at": _iso(row),
        "importance_sum": row["importance_sum"],
        "quality_sum": row["quality_sum"],
        "pairs": row["pairs"],
    }


def create_rearrangement(baseline_id_text: str, raw_payload: Any) -> Dict[str, Any]:
    baseline_id = _parse_uuid(baseline_id_text)
    with db.connection() as conn:
        baseline_row = conn.execute(
            "SELECT id, snapshot_id, pairs FROM baselines WHERE id = %s",
            (baseline_id,),
        ).fetchone()
        if baseline_row is None:
            raise NotFound()
        snapshot_row = _get_snapshot_row(conn, baseline_row["snapshot_id"])
        snapshot = _snapshot_from_payload(snapshot_row["payload"])

        # Validation happens inside the transaction but before any insert; a
        # rejected (illegal) fault list leaves no record behind.
        failed_ports = validate_failed_ports(
            raw_payload, set(snapshot["ports"]))

        result = plan_rearrangement(
            snapshot, baseline_row["pairs"], failed_ports)

        saved = conn.execute(
            "INSERT INTO rearrangements "
            "(baseline_id, snapshot_id, importance_sum, quality_sum, "
            " preserved_pairs, failed_ports, pairs, changes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id, created_at",
            (baseline_id, baseline_row["snapshot_id"],
             result["importance_sum"], result["quality_sum"],
             result["preserved_pairs"], result["failed_ports"],
             result["pairs"], result["changes"]),
        ).fetchone()
        conn.commit()

    return {
        "id": str(saved["id"]),
        "baseline_id": str(baseline_id),
        "snapshot_id": str(baseline_row["snapshot_id"]),
        "created_at": _iso(saved),
        "failed_ports": result["failed_ports"],
        "importance_sum": result["importance_sum"],
        "quality_sum": result["quality_sum"],
        "preserved_pairs": result["preserved_pairs"],
        "pairs": result["pairs"],
        "changes": result["changes"],
    }


def get_rearrangement(rearrangement_id_text: str) -> Dict[str, Any]:
    rearrangement_id = _parse_uuid(rearrangement_id_text)
    with db.connection() as conn:
        row = conn.execute(
            "SELECT id, baseline_id, snapshot_id, importance_sum, quality_sum, "
            "preserved_pairs, failed_ports, pairs, changes, created_at "
            "FROM rearrangements WHERE id = %s",
            (rearrangement_id,),
        ).fetchone()
    if row is None:
        raise NotFound()
    return {
        "id": str(row["id"]),
        "baseline_id": str(row["baseline_id"]),
        "snapshot_id": str(row["snapshot_id"]),
        "created_at": _iso(row),
        "failed_ports": row["failed_ports"],
        "importance_sum": row["importance_sum"],
        "quality_sum": row["quality_sum"],
        "preserved_pairs": row["preserved_pairs"],
        "pairs": row["pairs"],
        "changes": row["changes"],
    }
