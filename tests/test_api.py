"""End-to-end tests against the FastAPI app with PostgreSQL."""

import random
import time

from tests.conftest import baseline, make_snapshot, rearrange


# ---------------------------------------------------------------- health

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ------------------------------------------------------------- snapshots

def test_create_and_get_snapshot(client):
    resp = make_snapshot(
        client,
        signals=[("a", 5), ("b", 1)],
        ports=["P1", "P2"],
        edges=[("a", "P1", 10), ("b", "P2", 0)],
    )
    data = resp.json()
    assert "id" in data and "created_at" in data
    got = client.get(f"/snapshots/{data['id']}")
    assert got.status_code == 200
    body = got.json()
    assert body["id"] == data["id"]
    assert body["signals"] == [{"id": "a", "importance": 5},
                               {"id": "b", "importance": 1}]
    assert body["ports"] == [{"id": "P1"}, {"id": "P2"}]
    assert body["edges"] == [
        {"signal_id": "a", "port_id": "P1", "quality": 10},
        {"signal_id": "b", "port_id": "P2", "quality": 0},
    ]


def test_duplicate_edge_rejected(client):
    make_snapshot(
        client,
        signals=[("a", 5)], ports=["P1"],
        edges=[("a", "P1", 1), ("a", "P1", 2)],
        expected=422,
    )


def test_unknown_reference_rejected(client):
    make_snapshot(
        client, signals=[("a", 5)], ports=["P1"],
        edges=[("a", "P2", 1)], expected=422)
    make_snapshot(
        client, signals=[("a", 5)], ports=["P1"],
        edges=[("x", "P1", 1)], expected=422)


def test_value_ranges_enforced(client):
    bad_imp = [("a", 0), ("b", 1_000_001)]
    for sid, imp in bad_imp:
        make_snapshot(client, signals=[(sid, imp)], ports=["P1"],
                      edges=[], expected=422)
    for q in (-1, 1_000_001):
        make_snapshot(client, signals=[("a", 1)], ports=["P1"],
                      edges=[("a", "P1", q)], expected=422)
    # Boundary values are accepted.
    make_snapshot(client, signals=[("a", 1), ("b", 1_000_000)],
                  ports=["P1", "P2"],
                  edges=[("a", "P1", 0), ("b", "P2", 1_000_000)])


def test_duplicate_vertex_ids_rejected(client):
    resp = client.post("/snapshots", json={
        "signals": [{"id": "a", "importance": 1},
                    {"id": "a", "importance": 2}],
        "ports": [{"id": "P1"}],
        "edges": [],
    })
    assert resp.status_code == 422
    resp = client.post("/snapshots", json={
        "signals": [{"id": "a", "importance": 1}],
        "ports": [{"id": "P1"}, {"id": "P1"}],
        "edges": [],
    })
    assert resp.status_code == 422


def test_non_json_scalar_types_rejected(client):
    # booleans must not masquerade as integers
    resp = client.post("/snapshots", json={
        "signals": [{"id": "a", "importance": True}],
        "ports": [{"id": "P1"}],
        "edges": [],
    })
    assert resp.status_code == 422
    resp = client.post("/snapshots", json={
        "signals": [{"id": "a", "importance": 1}],
        "ports": [{"id": "P1"}],
        "edges": [{"signal_id": "a", "port_id": "P1", "quality": 1.5}],
    })
    assert resp.status_code == 422
    # empty / wrong-typed ids, extra fields, malformed JSON
    resp = client.post("/snapshots", json={
        "signals": [{"id": "", "importance": 1}],
        "ports": [], "edges": [],
    })
    assert resp.status_code == 422
    resp = client.post("/snapshots", json={
        "signals": [{"id": "a", "importance": 1, "extra": 0}],
        "ports": [], "edges": [],
    })
    assert resp.status_code == 422
    resp = client.post("/snapshots", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 422


def test_count_limits_enforced(client):
    resp = client.post("/snapshots", json={
        "signals": [{"id": i, "importance": 1} for i in range(401)],
        "ports": [{"id": "P0"}],
        "edges": [],
    })
    assert resp.status_code == 422
    resp = client.post("/snapshots", json={
        "signals": [],
        "ports": [{"id": i} for i in range(401)],
        "edges": [],
    })
    assert resp.status_code == 422
    # Exactly 400 ports referenced by 50001 edges.
    resp = client.post("/snapshots", json={
        "signals": [{"id": "s", "importance": 1}],
        "ports": [{"id": f"P{i}"} for i in range(400)],
        "edges": [{"signal_id": "s", "port_id": f"P{j % 400}",
                   "quality": 0} for j in range(50_001)],
    })
    assert resp.status_code == 422


def test_integer_ids_supported(client):
    data = make_snapshot(
        client, signals=[(1, 100), (2, 90)], ports=[10, 20],
        edges=[(1, 10, 5), (2, 20, 5)]).json()
    got = client.get(f"/snapshots/{data['id']}").json()
    assert got["signals"][0]["id"] == 1
    assert got["ports"][0]["id"] == 10


def test_empty_snapshot(client):
    data = make_snapshot(client, signals=[], ports=[], edges=[]).json()
    bl = baseline(client, data["id"])
    assert bl["importance_sum"] == 0
    assert bl["quality_sum"] == 0
    assert bl["pairs"] == []


# ------------------------------------------------------------- baselines

def _pair_map(pairs):
    return {p["signal_id"]: p["port_id"] for p in pairs}


def test_baseline_404(client):
    assert client.post("/snapshots/deadbeef/baselines").status_code == 404
    resp = client.post(
        "/snapshots/00000000-0000-0000-0000-000000000000/baselines")
    assert resp.status_code == 404


def test_baseline_greedy_importance_trap(client):
    # A naive greedy that locks the tempting first edge strands the unique
    # high-importance signal. Signal a can only use P1; if a left-to-right
    # greedy gives P1 to b (b's first choice), a is lost. Optimal keeps a.
    resp = make_snapshot(
        client,
        signals=[("a", 100), ("b", 90), ("c", 80)],
        ports=["P1", "P2"],
        edges=[
            ("a", "P1", 100),
            ("b", "P1", 100), ("b", "P2", 99),
            ("c", "P2", 100),
        ],
    )
    bl = baseline(client, resp.json()["id"])
    # a(100)+b(90)=190 is the reachable importance optimum (a has only P1,
    # and b can fall back to P2); a greedy that hands P1 to b first and then
    # cannot place a would answer 170 (b+c). Quality on the optimum is 199.
    assert bl["importance_sum"] == 190
    assert bl["quality_sum"] == 199
    pairs = _pair_map(bl["pairs"])
    assert pairs == {"a": "P1", "b": "P2"}
    assert "c" not in pairs


def test_baseline_quality_is_second_objective(client):
    # Both perfect matchings connect all signals; pick the high-quality one.
    resp = make_snapshot(
        client,
        signals=[("a", 10), ("b", 10)],
        ports=["P1", "P2"],
        edges=[
            ("a", "P1", 5), ("a", "P2", 9),
            ("b", "P1", 9), ("b", "P2", 5),
        ],
    )
    bl = baseline(client, resp.json()["id"])
    assert bl["importance_sum"] == 20
    assert bl["quality_sum"] == 18
    pairs = _pair_map(bl["pairs"])
    assert pairs == {"a": "P2", "b": "P1"}


def test_baseline_deterministic_and_stable_on_read(client):
    resp = make_snapshot(
        client,
        signals=[("a", 10), ("b", 10)],
        ports=["P1", "P2"],
        edges=[
            ("a", "P1", 7), ("a", "P2", 7),
            ("b", "P1", 7), ("b", "P2", 7),
        ],
    )
    sid = resp.json()["id"]
    first = _pair_map(baseline(client, sid)["pairs"])
    second = _pair_map(baseline(client, sid)["pairs"])
    assert first == second                      # ties never drift
    # retrieve records and confirm stored pairs never change
    for _ in range(2):
        rows = client.post(f"/snapshots/{sid}/baselines").json()
        got = client.get(f"/baselines/{rows['id']}").json()
        assert _pair_map(got["pairs"]) == first


def test_baseline_get_404(client):
    assert client.get("/baselines/not-a-uuid").status_code == 404


# --------------------------------------------------------- rearrangements

def test_rearrangement_failure_drops_signal(client):
    resp = make_snapshot(
        client,
        signals=[("a", 100), ("b", 90)],
        ports=["P1", "P2"],
        edges=[("a", "P1", 100), ("b", "P1", 100), ("b", "P2", 100)],
    )
    bl = baseline(client, resp.json()["id"])
    assert _pair_map(bl["pairs"]) == {"a": "P1", "b": "P2"}

    rr = rearrange(client, bl["id"], ["P2"])
    pairs = _pair_map(rr["pairs"])
    assert pairs == {"a": "P1"}                # importance beats b
    assert rr["importance_sum"] == 100
    assert rr["quality_sum"] == 100
    assert rr["preserved_pairs"] == 1
    assert rr["failed_ports"] == ["P2"]
    assert rr["changes"] == [
        {"signal_id": "b", "from_port": "P2",
         "to_port": None, "type": "disconnected"},
    ]


def test_rearrangement_switch_keeps_important_signal(client):
    # a can only use P1; after a different port fails, b must give P1 up.
    resp = make_snapshot(
        client,
        signals=[("a", 100), ("b", 90)],
        ports=["P1", "P2"],
        edges=[("a", "P1", 100), ("b", "P1", 100), ("b", "P2", 100)],
    )
    bl = baseline(client, resp.json()["id"])
    rr = rearrange(client, bl["id"], ["P1"])
    pairs = _pair_map(rr["pairs"])
    assert pairs == {"b": "P2"}
    assert rr["importance_sum"] == 90
    assert rr["preserved_pairs"] == 1
    assert rr["changes"] == [
        {"signal_id": "a", "from_port": "P1",
         "to_port": None, "type": "disconnected"},
    ]


def test_rearrangement_quality_beats_preservation(client):
    # Keeping both old pairs after P2 fails is only possible with the two
    # low-quality edges; optimum switches even though it preserves fewer.
    resp = make_snapshot(
        client,
        signals=[("a", 10), ("b", 10)],
        ports=["P1", "P2", "P3"],
        edges=[
            ("a", "P1", 1), ("a", "P3", 10),
            ("b", "P1", 10), ("b", "P2", 11), ("b", "P3", 1),
        ],
    )
    bl = baseline(client, resp.json()["id"])
    assert _pair_map(bl["pairs"]) == {"a": "P3", "b": "P2"}
    assert bl["quality_sum"] == 21

    rr = rearrange(client, bl["id"], ["P2"])
    pairs = _pair_map(rr["pairs"])
    assert pairs == {"a": "P3", "b": "P1"}
    assert rr["importance_sum"] == 20
    assert rr["quality_sum"] == 20
    assert rr["preserved_pairs"] == 1          # a kept; b switched
    switch = {c["signal_id"]: c for c in rr["changes"]}
    assert switch["b"] == {
        "signal_id": "b", "from_port": "P2",
        "to_port": "P1", "type": "switched"}
    assert "a" not in switch


def test_rearrangement_preservation_is_third_objective(client):
    # After P3 fails the two imp/quality-equal full rewirings tie; the one
    # preserving more baseline pairs must win.
    resp = make_snapshot(
        client,
        signals=[("a", 10), ("b", 10), ("c", 10)],
        ports=["P1", "P2", "P3", "P4"],
        edges=[
            ("a", "P1", 10), ("a", "P4", 10),
            ("b", "P2", 10),
            ("c", "P3", 10), ("c", "P1", 10), ("c", "P4", 10),
        ],
    )
    bl = baseline(client, resp.json()["id"])
    assert _pair_map(bl["pairs"]) == {"a": "P1", "b": "P2", "c": "P3"}

    rr = rearrange(client, bl["id"], ["P3"])
    pairs = _pair_map(rr["pairs"])
    assert pairs == {"a": "P1", "b": "P2", "c": "P4"}
    assert rr["importance_sum"] == 30
    assert rr["quality_sum"] == 30
    assert rr["preserved_pairs"] == 2
    assert rr["changes"] == [
        {"signal_id": "c", "from_port": "P3",
         "to_port": "P4", "type": "switched"},
    ]


def test_rearrangement_no_failure_preserves_baseline(client):
    resp = make_snapshot(
        client,
        signals=[("a", 10), ("b", 20)],
        ports=["P1", "P2"],
        edges=[("a", "P1", 3), ("a", "P2", 8),
               ("b", "P1", 8), ("b", "P2", 3)],
    )
    bl = baseline(client, resp.json()["id"])
    old = _pair_map(bl["pairs"])
    rr = rearrange(client, bl["id"], [])
    assert _pair_map(rr["pairs"]) == old
    assert rr["changes"] == []
    assert rr["preserved_pairs"] == len(old)
    assert rr["failed_ports"] == []


def test_rearrangement_illegal_fault_rejected_and_unrecorded(client):
    resp = make_snapshot(
        client, signals=[("a", 1)], ports=["P1"],
        edges=[("a", "P1", 1)])
    bl = baseline(client, resp.json()["id"])

    assert client.post(
        f"/baselines/{bl['id']}/rearrangements",
        json={"failed_ports": ["PX"]}).status_code == 422
    assert client.post(
        f"/baselines/{bl['id']}/rearrangements",
        json={"failed_ports": ["P1", "P1"]}).status_code == 422
    assert client.post(
        f"/baselines/{bl['id']}/rearrangements",
        json={"failed_ports": [True]}).status_code == 422
    assert client.post(
        f"/baselines/{bl['id']}/rearrangements",
        content=b"not-json",
        headers={"content-type": "application/json"}).status_code == 422
    # A valid rearrangement still works and keeps deterministic results.
    rr1 = rearrange(client, bl["id"], ["P1"])
    rr2 = rearrange(client, bl["id"], ["P1"])
    assert rr1["pairs"] == rr2["pairs"]
    got = client.get(f"/rearrangements/{rr1['id']}").json()
    assert got["pairs"] == rr1["pairs"]
    assert got["changes"] == rr1["changes"]
    assert client.get("/rearrangements/unknown").status_code == 404
    assert client.post("/baselines/unknown/rearrangements",
                       json={"failed_ports": []}).status_code == 404


def test_rearrangement_uses_each_signal_and_port_once(client):
    random.seed(11)
    signals = [(f"s{i}", random.randint(1, 1_000_000)) for i in range(60)]
    ports = [f"p{i}" for i in range(60)]
    edges = []
    for s, _ in signals:
        for p in random.sample(ports, k=15):
            edges.append((s, p, random.randint(0, 1_000_000)))
    sid = make_snapshot(client, signals, ports, edges).json()["id"]
    bl = baseline(client, sid)
    rr = rearrange(client, bl["id"], random.sample(ports, 10))
    used_s = [p["signal_id"] for p in rr["pairs"]]
    used_p = [p["port_id"] for p in rr["pairs"]]
    assert len(used_s) == len(set(used_s))
    assert len(used_p) == len(set(used_p))
    assert set(used_p).isdisjoint(set(rr["failed_ports"]))


# ------------------------------------------------------------- performance

def test_max_size_operations_within_two_seconds(client):
    random.seed(2026)
    n = m = 400
    signals = [(i, random.randint(1, 1_000_000)) for i in range(n)]
    ports = list(range(m))
    edges = {}
    while len(edges) < 50_000:
        s = random.randrange(n)
        p = random.randrange(m)
        edges[(s, p)] = random.randint(0, 1_000_000)
    edge_list = [(s, p, q) for (s, p), q in edges.items()]

    sid = make_snapshot(client, signals, ports, edge_list).json()["id"]

    t0 = time.perf_counter()
    bl = baseline(client, sid)
    baseline_elapsed = time.perf_counter() - t0
    assert baseline_elapsed < 2.0, baseline_elapsed
    assert len(bl["pairs"]) == 400

    failed = list(range(50))
    t0 = time.perf_counter()
    rr = rearrange(client, bl["id"], failed)
    rearrange_elapsed = time.perf_counter() - t0
    assert rearrange_elapsed < 2.0, rearrange_elapsed
    assert len(rr["pairs"]) == 350
    assert set(p["port_id"] for p in rr["pairs"]).isdisjoint(failed)
    assert {c["signal_id"] for c in rr["changes"]} <= set(range(400))
