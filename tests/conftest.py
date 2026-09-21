import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://app:app@localhost:5432/app")

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def make_snapshot(client, signals, ports, edges, expected=200):
    payload = {
        "signals": [{"id": s, "importance": i} for s, i in signals],
        "ports": [{"id": p} for p in ports],
        "edges": [{"signal_id": s, "port_id": p, "quality": q}
                  for s, p, q in edges],
    }
    resp = client.post("/snapshots", json=payload)
    assert resp.status_code == expected, resp.text
    return resp


def baseline(client, snapshot_id):
    resp = client.post(f"/snapshots/{snapshot_id}/baselines")
    assert resp.status_code == 200, resp.text
    return resp.json()


def rearrange(client, baseline_id, failed, expected=200):
    resp = client.post(
        f"/baselines/{baseline_id}/rearrangements",
        json={"failed_ports": failed},
    )
    assert resp.status_code == expected, resp.text
    return resp.json()
