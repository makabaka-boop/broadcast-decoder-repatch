"""Self-contained maximum-weight bipartite matching (rectangular, vertices may
stay unmatched).

A native C++ implementation of the same formulation is used when available
(built during image build); a pure-Python implementation with identical
semantics serves as fallback.  Both run the potential / Hungarian method on a
square matrix where every row owns a private zero-cost dummy column, encoding
optional matching exactly (a dummy can never be shared between rows).

Tie-breaking is deterministic: columns are scanned in fixed canonical order
with strict comparisons, so equal-valued optima never drift between runs.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

# Cost for incompatible real cells. Far above any feasible total.
_INF = 10**24
_ZERO = 0

try:  # Prefer the compiled core when the image built it.
    from . import _assignment_native as _native  # type: ignore
except ImportError:  # pragma: no cover - exercised only without the build
    _native = None


def _hungarian_py(n: int, m: int,
                  rows: Sequence[Sequence[int]]) -> List[int]:
    u = [0] * (n + 1)
    v = [0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    inf = _INF

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            row0 = rows[i0 - 1]
            ui0 = u[i0]
            delta = inf
            j1 = 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = row0[j - 1] - ui0 - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    result = [-1] * m
    for j in range(1, m + 1):
        r = p[j]
        if r and rows[r - 1][j - 1] < inf // 2:
            result[j - 1] = r - 1
    return result


def _run_hungarian(rows_count: int, size: int,
                   rows: List[List[int]]) -> List[int]:
    if _native is not None:
        # The native module uses None for incompatible cells; real cells are
        # already negated weights / zeros for private dummies.
        native_rows = [[None if cell >= _INF // 2 else cell
                        for cell in row] for row in rows]
        return list(_native.hungarian(native_rows))
    return _hungarian_py(rows_count, size, rows)


def max_weight_matching(left_ids: List, right_ids: List,
                        weights: Dict[Tuple[int, int], int]
                        ) -> List[Tuple[object, object]]:
    """Maximum-weight matching between indexed vertices.

    ``weights`` maps ``(left_index, right_index)`` to a non-negative integer.
    Each vertex is free to stay unmatched.  Returns matched
    ``(left_id, right_id)`` pairs in canonical left-id order.

    Optional rectangular matching is reduced to a perfect square assignment
    using dummy vertices on both sides.  With ``n = min(|L|, |R|)`` real rows,
    ``m = max(|L|, |R|)`` real columns (transposing if needed), build an
    ``(n + m) x (n + m)`` matrix:

      * real row / real column: ``-weight`` for an edge, otherwise infinity;
      * every cell touching a dummy vertex: 0.

    The square assignment always exists.  Positive-weight real edges beat the
    zero dummy option, while a real row with no beneficial real edge is free
    to use a dummy column (and vice versa), so real vertices may stay
    unmatched.  Having dummy rows *and* dummy columns keeps both available
    during alternating-path rearrangements even when one real side has no
    usable edges.
    """
    n_real = len(left_ids)
    m_real = len(right_ids)
    if n_real == 0 or m_real == 0:
        return []

    transposed = n_real > m_real
    if not transposed:
        n, m = n_real, m_real
    else:
        n, m = m_real, n_real

    size = n + m
    rows: List[List[int]] = [[_ZERO] * size for _ in range(size)]
    for r in range(n):
        for c in range(m):
            rows[r][c] = _INF

    for (a, b), w in weights.items():
        if not transposed:
            rows[a][b] = -w
        else:
            rows[b][a] = -w

    matched = _run_hungarian(size, size, rows)

    pairs: List[Tuple[object, object]] = []
    for c in range(m):
        r = matched[c]
        if 0 <= r < n:
            if not transposed:
                pairs.append((left_ids[r], right_ids[c]))
            else:
                pairs.append((left_ids[c], right_ids[r]))

    pairs.sort(key=lambda pair: pair[0])
    return pairs
