import os
import pytest
os.environ['SIBYL_OFFICIAL']='0'
os.environ.setdefault('DATABASE_URL','postgresql://vesper:vesper@localhost:5432/vesper_test')
os.environ['VESPER_AUTH_REQUIRED']='true'
os.environ['VESPER_API_KEY']='client-test-key'
os.environ['VESPER_ADMIN_KEY']='admin-test-key'
os.environ['VESPER_SETTLEMENT_KEY']='settlement-test-key'
os.environ['VESPER_OPERATOR_KEY']='operator-test-key'


@pytest.fixture(scope='session')
def pg_connection_factory():
    """Opt-in database fixture; never falls back to the application DATABASE_URL."""
    if os.getenv('VESPER_RUN_DB_TESTS', '0').lower() not in {'1', 'true', 'yes'}:
        pytest.skip('opt-in database tests require VESPER_RUN_DB_TESTS=1')
    url=os.getenv('VESPER_TEST_DATABASE_URL', '').strip()
    if not url:
        pytest.skip('VESPER_TEST_DATABASE_URL is required for database tests')
    if 'supabase' in url.lower() and os.getenv('VESPER_ALLOW_SUPABASE_TEST_DB') != '1':
        pytest.fail('refusing Supabase database without VESPER_ALLOW_SUPABASE_TEST_DB=1')
    psycopg=pytest.importorskip('psycopg')
    def connect(): return psycopg.connect(url, autocommit=False)
    with connect() as connection: connection.execute('SELECT 1')
    return connect


@pytest.fixture()
def pg_connection(pg_connection_factory):
    with pg_connection_factory() as connection:
        yield connection
        connection.rollback()


@pytest.fixture(scope='session')
def database_schema(pg_connection_factory):
    with pg_connection_factory() as connection:
        rows=connection.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall()
    assert rows, 'schema_migrations is empty; run migrations before DB tests'
    return [row[0] for row in rows]
