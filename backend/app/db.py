from __future__ import annotations
import os
import threading
from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

class PostgresDatabase:
    _pool = None
    _pool_lock = threading.Lock()
    _schema_ready = False
    def __init__(self):
        self.database_url = os.getenv('DATABASE_URL','').strip()
        if not self.database_url: raise RuntimeError('DATABASE_URL is required; SQLite persistence has been removed.')
        self._ensure_pool(); self._ensure_schema()
    def _ensure_pool(self):
        if PostgresDatabase._pool is not None: return
        with PostgresDatabase._pool_lock:
            if PostgresDatabase._pool is None:
                PostgresDatabase._pool=ConnectionPool(conninfo=self.database_url,min_size=1,max_size=max(1,int(os.getenv('DATABASE_POOL_MAX','4'))),kwargs={'autocommit':False,'row_factory':dict_row},open=True);PostgresDatabase._pool.wait()
    @property
    def pool(self): return PostgresDatabase._pool
    @contextmanager
    def connection(self):
        with self.pool.connection() as connection: yield connection
    @staticmethod
    def json(value): return Jsonb(value)
    def ping(self):
        with self.connection() as connection:
            connection.execute('SELECT 1')
        return True
    def _ensure_schema(self):
        if PostgresDatabase._schema_ready: return
        with PostgresDatabase._pool_lock:
            if PostgresDatabase._schema_ready: return
            statements=[
             'CREATE TABLE IF NOT EXISTS memory (tier TEXT NOT NULL, key TEXT NOT NULL, value JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL, PRIMARY KEY (tier,key))',
             'CREATE TABLE IF NOT EXISTS journal (seq BIGSERIAL PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL, event TEXT NOT NULL, payload JSONB NOT NULL)',
             'CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE, decision_id TEXT NOT NULL, mode TEXT NOT NULL, market_id TEXT NOT NULL, side TEXT NOT NULL, requested_size DOUBLE PRECISION NOT NULL, limit_price DOUBLE PRECISION NOT NULL, status TEXT NOT NULL, filled_size DOUBLE PRECISION NOT NULL, average_fill_price DOUBLE PRECISION, venue_order_id TEXT, error TEXT, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)',
             'CREATE INDEX IF NOT EXISTS idx_orders_decision ON orders(decision_id)','CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)',
             'CREATE TABLE IF NOT EXISTS market_snapshots (id BIGSERIAL PRIMARY KEY, market_id TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL, payload_hash TEXT NOT NULL UNIQUE)','CREATE INDEX IF NOT EXISTS idx_market_snapshots_observed ON market_snapshots(observed_at)',
             'CREATE TABLE IF NOT EXISTS market_observations (id BIGSERIAL PRIMARY KEY, market_id TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL, payload_hash TEXT NOT NULL, valid BOOLEAN NOT NULL, book_valid BOOLEAN NOT NULL DEFAULT FALSE, book_sequence BIGINT)','CREATE INDEX IF NOT EXISTS idx_market_observations_observed ON market_observations(observed_at)',
             'CREATE TABLE IF NOT EXISTS pipeline_health (id INTEGER PRIMARY KEY, last_tick_at TIMESTAMPTZ, last_success_at TIMESTAMPTZ, last_error_at TIMESTAMPTZ, last_error TEXT, error_count INTEGER NOT NULL DEFAULT 0, last_markets INTEGER NOT NULL DEFAULT 0, last_books INTEGER NOT NULL DEFAULT 0)','INSERT INTO pipeline_health(id) VALUES (1) ON CONFLICT (id) DO NOTHING']
            with self.connection() as connection:
                for statement in statements: connection.execute(statement)
            PostgresDatabase._schema_ready=True
