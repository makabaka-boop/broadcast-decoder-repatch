"""Strict validation for snapshot and re-arrangement payloads.

Only ordinary JSON types are accepted.  Any structural problem, out-of-range
value, duplicate edge / id or unknown reference raises :class:`ValidationError`
(the HTTP layer turns it into a 422 response); a rejected request never leaves
a persisted record.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

MAX_SIGNALS = 400
MAX_PORTS = 400
MAX_EDGES = 50_000
MIN_IMPORTANCE = 1
MAX_IMPORTANCE = 1_000_000
MIN_QUALITY = 0
MAX_QUALITY = 1_000_000


class ValidationError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _err(message: str) -> None:
    raise ValidationError(message)


def _is_plain_int(value: Any) -> bool:
    # ``bool`` is a subclass of int and must be rejected explicitly; floats
    # (including 1.0) are not ordinary JSON integers here.
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_id(value: Any) -> bool:
    if _is_plain_int(value):
        return True
    return isinstance(value, str) and len(value) > 0


def _check_object(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        _err(f"{label} must be an object")
    return value


def _check_extra(obj: Dict[str, Any], allowed: set, label: str) -> None:
    extra = set(obj) - allowed
    if extra:
        _err(f"{label} contains unexpected field: {sorted(extra)[0]}")


def _check_vertex_list(value: Any, label: str, id_field: str,
                       weight_field: str | None) -> List[Tuple[Any, int]]:
    if not isinstance(value, list):
        _err(f"{label} must be a list")
    count = MAX_SIGNALS if weight_field else MAX_PORTS
    if len(value) > count:
        _err(f"{label} count exceeds maximum of {count}")

    vertices: List[Tuple[Any, int]] = []
    seen = set()
    for idx, item in enumerate(value):
        item = _check_object(item, f"{label}[{idx}]")
        extra = {id_field} | ({weight_field} if weight_field else set())
        _check_extra(item, extra, f"{label}[{idx}]")
        if id_field not in item:
            _err(f"{label}[{idx}] is missing field {id_field!r}")
        vid = item[id_field]
        if not _valid_id(vid):
            _err(f"{label}[{idx}].{id_field} must be a non-empty string or integer")
        if vid in seen:
            _err(f"duplicate {id_field}: {vid!r}")
        seen.add(vid)
        if weight_field:
            if weight_field not in item:
                _err(f"{label}[{idx}] is missing field {weight_field!r}")
            imp = item[weight_field]
            if not _is_plain_int(imp):
                _err(f"{label}[{idx}].{weight_field} must be an integer")
            if not (MIN_IMPORTANCE <= imp <= MAX_IMPORTANCE):
                _err(f"{label}[{idx}].{weight_field} out of range "
                     f"[{MIN_IMPORTANCE}, {MAX_IMPORTANCE}]")
            vertices.append((vid, imp))
        else:
            vertices.append((vid, 0))
    return vertices


def validate_snapshot(payload: Any) -> Dict[str, Any]:
    root = _check_object(payload, "request body")
    _check_extra(root, {"signals", "ports", "edges"}, "request body")
    if "signals" not in root:
        _err("request body is missing field 'signals'")
    if "ports" not in root:
        _err("request body is missing field 'ports'")
    if "edges" not in root:
        _err("request body is missing field 'edges'")

    signals = _check_vertex_list(root["signals"], "signals",
                                 "id", "importance")
    ports = _check_vertex_list(root["ports"], "ports", "id", None)
    if len(signals) > MAX_SIGNALS:
        _err(f"signals count exceeds maximum of {MAX_SIGNALS}")
    if len(ports) > MAX_PORTS:
        _err(f"ports count exceeds maximum of {MAX_PORTS}")

    signal_ids = {vid for vid, _ in signals}
    port_ids = {vid for vid, _ in ports}

    raw_edges = root["edges"]
    if not isinstance(raw_edges, list):
        _err("edges must be a list")
    if len(raw_edges) > MAX_EDGES:
        _err(f"edges count exceeds maximum of {MAX_EDGES}")

    edges: List[Tuple[Any, Any, int]] = []
    seen_edges = set()
    for idx, item in enumerate(raw_edges):
        item = _check_object(item, f"edges[{idx}]")
        _check_extra(item, {"signal_id", "port_id", "quality"},
                     f"edges[{idx}]")
        for field in ("signal_id", "port_id", "quality"):
            if field not in item:
                _err(f"edges[{idx}] is missing field {field!r}")
        sid = item["signal_id"]
        pid = item["port_id"]
        quality = item["quality"]
        if not _valid_id(sid):
            _err(f"edges[{idx}].signal_id must be a non-empty string or integer")
        if not _valid_id(pid):
            _err(f"edges[{idx}].port_id must be a non-empty string or integer")
        if not _is_plain_int(quality):
            _err(f"edges[{idx}].quality must be an integer")
        if not (MIN_QUALITY <= quality <= MAX_QUALITY):
            _err(f"edges[{idx}].quality out of range "
                 f"[{MIN_QUALITY}, {MAX_QUALITY}]")
        if sid not in signal_ids:
            _err(f"edges[{idx}] references unknown signal_id {sid!r}")
        if pid not in port_ids:
            _err(f"edges[{idx}] references unknown port_id {pid!r}")
        key = (sid, pid)
        if key in seen_edges:
            _err(f"duplicate edge ({sid!r}, {pid!r})")
        seen_edges.add(key)
        edges.append((sid, pid, quality))

    return {
        "signals": signals,          # [(id, importance)]
        "ports": [pid for pid, _ in ports],
        "edges": edges,              # [(signal_id, port_id, quality)]
    }


def validate_failed_ports(payload: Any, known_ports: set) -> List[Any]:
    root = _check_object(payload, "request body")
    _check_extra(root, {"failed_ports"}, "request body")
    if "failed_ports" not in root:
        _err("request body is missing field 'failed_ports'")
    raw = root["failed_ports"]
    if not isinstance(raw, list):
        _err("failed_ports must be a list")

    failed: List[Any] = []
    seen = set()
    for idx, pid in enumerate(raw):
        if not _valid_id(pid):
            _err(f"failed_ports[{idx}] must be a non-empty string or integer")
        if pid not in known_ports:
            _err(f"failed_ports[{idx}] references unknown port_id {pid!r}")
        if pid in seen:
            _err(f"failed_ports contains duplicate port_id {pid!r}")
        seen.add(pid)
        failed.append(pid)
    return failed
