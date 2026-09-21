"""PostgreSQL access: a small psycopg pool and schema bootstrap."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS baselines (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id     UUID NOT NULL REFERENCES snapshots(id),
    importance_sum  BIGINT NOT NULL,
    quality_sum     BIGINT NOT NULL,
    pairs           JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rearrangements (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    baseline_id      UUID NOT NULL REFERENCES baselines(id),
    snapshot_id      UUID NOT NULL REFERENCES snapshots(id),
    importance_sum   BIGINT NOT NULL,
    quality_sum      BIGINT NOT NULL,
    preserved_pairs  INTEGER NOT NULL,
    failed_ports     JSONB NOT NULL,
    pairs            JSONB NOT NULL,
    changes          JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def dsn() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://app:app@localhost:5432/app",
    )


def init_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            dsn(),
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        with _pool.connection() as conn:
            conn.execute(SCHEMA)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    pool = init_pool()
    with pool.connection() as conn:
        conn.execute("SET statement_timeout = '10s'")
        yield conn
