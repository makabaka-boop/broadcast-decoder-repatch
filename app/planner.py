"""Baseline assignment and fault re-arrangement planning.

Objectives are lexicographic and are encoded into a single integer weight so
the generic maximum-weight matcher returns the lexicographic optimum:

* baseline:     importance-sum  then  quality-sum
* re-arrange:   importance-sum  then  quality-sum  then  preserved-baseline-pairs

The multipliers dominate the largest possible contribution of every lower
priority tier, so no lower-tier gain can ever compensate a higher-tier loss.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .assignment import max_weight_matching

# A single signal can contribute at most 1_000_000 importance; quality is in
# [0, 1_000_000] and a matching holds at most 400 edges, so quality-sum and
# preserved-pair count are both bounded well below the multipliers below.
_IMP_SCALE = 10**9          # > max quality-sum (400 * 1_000_000)
_QUAL_SCALE = 1000          # > max preserved pairs (400)


def _canonical_key(value: Any):
    # Deterministic ordering independent of insertion order.  JSON allows ids
    # to be strings or integers; same-type ordering is natural, strings sort
    # before integers when mixed.
    return (0, value) if isinstance(value, str) else (1, value)


def _indexed(snapshot: Dict[str, Any]):
    signals = sorted(snapshot["signals"], key=lambda s: _canonical_key(s[0]))
    ports = sorted(snapshot["ports"], key=_canonical_key)
    s_idx = {sid: i for i, (sid, _) in enumerate(signals)}
    p_idx = {pid: i for i, pid in enumerate(ports)}
    return signals, ports, s_idx, p_idx


def _sums(pairs: List[Tuple[Any, Any]], imp_by_signal: Dict[Any, int],
          quality: Dict[Tuple[Any, Any], int]):
    imp_sum = sum(imp_by_signal[a] for a, _ in pairs)
    qual_sum = sum(quality[(a, b)] for a, b in pairs)
    return imp_sum, qual_sum


def _serialize(pairs: List[Tuple[Any, Any]]) -> List[Dict[str, Any]]:
    return [{"signal_id": a, "port_id": b} for a, b in pairs]


def plan_baseline(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    signals, ports, s_idx, p_idx = _indexed(snapshot)
    imp_by_signal = {sid: imp for sid, imp in signals}
    quality = {(sid, pid): q for sid, pid, q in snapshot["edges"]}

    weights = {}
    for sid, pid, q in snapshot["edges"]:
        weights[(s_idx[sid], p_idx[pid])] = (
            imp_by_signal[sid] * _IMP_SCALE + q
        )

    pair_indices = max_weight_matching(
        list(range(len(signals))), list(range(len(ports))), weights)
    pairs = [(signals[r][0], ports[c]) for r, c in pair_indices]

    imp_sum, qual_sum = _sums(pairs, imp_by_signal, quality)
    return {
        "importance_sum": imp_sum,
        "quality_sum": qual_sum,
        "pairs": _serialize(pairs),
        "_pair_map": dict(pairs),
    }


def plan_rearrangement(snapshot: Dict[str, Any],
                       baseline_pairs: List[Dict[str, Any]],
                       failed_ports: List[Any]) -> Dict[str, Any]:
    signals, ports, s_idx, p_idx = _indexed(snapshot)
    imp_by_signal = {sid: imp for sid, imp in signals}
    quality = {(sid, pid): q for sid, pid, q in snapshot["edges"]}
    previous = {item["signal_id"]: item["port_id"] for item in baseline_pairs}

    failed = set(failed_ports)
    active_ports = [pid for pid in ports if pid not in failed]
    active_idx = {pid: i for i, pid in enumerate(active_ports)}

    weights = {}
    for sid, pid, q in snapshot["edges"]:
        if pid in failed:
            continue
        preserved = 1 if previous.get(sid) == pid else 0
        weights[(s_idx[sid], active_idx[pid])] = (
            imp_by_signal[sid] * _IMP_SCALE * _QUAL_SCALE
            + q * _QUAL_SCALE
            + preserved
        )

    pair_indices = max_weight_matching(
        list(range(len(signals))), list(range(len(active_ports))), weights)
    pairs = [(signals[r][0], active_ports[c]) for r, c in pair_indices]

    imp_sum, qual_sum = _sums(pairs, imp_by_signal, quality)
    new_map = dict(pairs)

    changes: List[Dict[str, Any]] = []
    ordered_signals = [sid for sid, _ in signals]
    for sid in ordered_signals:
        old = previous.get(sid)
        new = new_map.get(sid)
        if old == new:
            continue
        entry: Dict[str, Any] = {"signal_id": sid}
        if old is None:
            entry["from_port"] = None
            entry["to_port"] = new
            entry["type"] = "connected"
        elif new is None:
            entry["from_port"] = old
            entry["to_port"] = None
            entry["type"] = "disconnected"
        else:
            entry["from_port"] = old
            entry["to_port"] = new
            entry["type"] = "switched"
        changes.append(entry)

    return {
        "importance_sum": imp_sum,
        "quality_sum": qual_sum,
        "preserved_pairs": sum(
            1 for sid, pid in pairs if previous.get(sid) == pid),
        "pairs": _serialize(pairs),
        "changes": changes,
        "failed_ports": list(failed_ports),
    }
