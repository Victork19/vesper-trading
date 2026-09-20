from __future__ import annotations
import os
import threading
from pathlib import Path
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
                # A pipeline lease holds one connection while the tick uses
                # short-lived connections for writes and reads. Keep at least
                # two pooled connections so a valid DATABASE_POOL_MAX=1 cannot
                # deadlock the worker.
                statement_timeout=max(1000,int(os.getenv('DATABASE_STATEMENT_TIMEOUT_MS','15000')))
                lock_timeout=max(100,int(os.getenv('DATABASE_LOCK_TIMEOUT_MS','5000')))
                idle_timeout=max(1000,int(os.getenv('DATABASE_IDLE_TRANSACTION_TIMEOUT_MS','30000')))
                connect_timeout=max(1,int(os.getenv('DATABASE_CONNECT_TIMEOUT_SECONDS','10')))
                pool_wait_timeout=max(1,float(os.getenv('DATABASE_POOL_WAIT_TIMEOUT_SECONDS',str(connect_timeout * 2))))
                options=f'-c statement_timeout={statement_timeout} -c lock_timeout={lock_timeout} -c idle_in_transaction_session_timeout={idle_timeout}'
                pool=ConnectionPool(conninfo=self.database_url,min_size=2,max_size=max(2,int(os.getenv('DATABASE_POOL_MAX','4'))),timeout=pool_wait_timeout,reconnect_timeout=pool_wait_timeout,kwargs={'autocommit':False,'row_factory':dict_row,'connect_timeout':connect_timeout,'options':options},open=True)
                PostgresDatabase._pool=pool
                try:
                    # psycopg_pool otherwise keeps reconnecting for its default
                    # five minutes, which makes API imports and health checks
                    # look hung when Postgres is unavailable.
                    pool.wait(timeout=pool_wait_timeout)
                except Exception as exc:
                    pool.close()
                    PostgresDatabase._pool=None
                    raise RuntimeError(f'Unable to connect to PostgreSQL within {pool_wait_timeout:g}s') from exc
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

    def reconciliation_health(self):
        with self.connection() as connection:
            row=connection.execute('SELECT * FROM reconciliation_health WHERE id=1').fetchone()
        if not row:return None
        data=dict(row)
        for key in ('last_cycle_started_at','last_cycle_completed_at','last_success_at','last_enumeration_at','last_account_at','last_error_at','updated_at'):
            if hasattr(data.get(key),'isoformat'):data[key]=data[key].isoformat()
        return data

    def reconciliation_is_healthy(self, max_age_seconds=None):
        row=self.reconciliation_health()
        if not row or not row.get('last_success_at'):return False
        age=max(1,float(max_age_seconds or os.getenv('RECONCILIATION_HEALTH_MAX_AGE_SECONDS','30')))
        from datetime import datetime, timezone
        try:return (datetime.now(timezone.utc)-datetime.fromisoformat(str(row['last_success_at']).replace('Z','+00:00'))).total_seconds() <= age and not row.get('stale',True)
        except (TypeError,ValueError):return False
    def _ensure_schema(self):
        if PostgresDatabase._schema_ready: return
        with PostgresDatabase._pool_lock:
            if PostgresDatabase._schema_ready: return
            migration_dir=Path(__file__).resolve().parent.parent / 'migrations'
            migrations=sorted(migration_dir.glob('*.sql'))
            if not migrations: raise RuntimeError('No database migrations were found')
            with self.connection() as connection:
                # Serialize migrations across API, pipeline, and reconciler
                # replicas. The process-local flag is only a fast path; the
                # database advisory lock is the authority.
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('vesper:schema_migrations',0))")
                connection.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())')
                applied={row['version'] for row in connection.execute('SELECT version FROM schema_migrations').fetchall()}
                for migration in migrations:
                    version=migration.stem
                    if version in applied: continue
                    sql=migration.read_text(encoding='utf-8')
                    connection.execute(sql)
                    connection.execute('INSERT INTO schema_migrations(version) VALUES(%s)',(version,))
            PostgresDatabase._schema_ready=True
