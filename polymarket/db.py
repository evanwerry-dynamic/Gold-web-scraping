"""PostgreSQL persistence layer. Only active when DATABASE_URL env var is set.

H7: Uses ThreadedConnectionPool for thread safety — each operation gets its own
connection from the pool and returns it after use, preventing cross-thread cursor
contamination from the previous global singleton approach.
"""
import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_pool = None


def _get_db_url() -> str | None:
    """Resolve PostgreSQL URL from various Railway/Postgres env var names."""
    url = (
        os.getenv("DATABASE_URL")
        or os.getenv("POSTGRES_URL")
        or os.getenv("POSTGRESQL_URL")
    )
    if url:
        return url

    # Construct from individual Railway/Postgres env vars
    host     = os.getenv("PGHOST")     or os.getenv("POSTGRES_HOST", "")
    port     = os.getenv("PGPORT")     or os.getenv("POSTGRES_PORT", "5432")
    user     = os.getenv("PGUSER")     or os.getenv("POSTGRES_USER", "")
    password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD", "")
    dbname   = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB", "")
    if host and user and dbname:
        return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
    return None


def _get_pool():
    """Return the ThreadedConnectionPool, initializing it on first call."""
    global _pool
    if _pool is not None:
        return _pool

    import psycopg2.pool

    url = _get_db_url()
    if not url:
        raise RuntimeError(
            "No database URL found. Set DATABASE_URL, POSTGRES_URL, "
            "or PGHOST/PGUSER/PGPASSWORD/PGDATABASE env vars."
        )

    _pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=1, maxconn=5, dsn=url, connect_timeout=5
    )
    return _pool


class _PoolConn:
    """Context manager: get a connection from the pool, return it after use."""

    def __enter__(self):
        self._pool = _get_pool()
        self._conn = self._pool.getconn()
        self._conn.autocommit = True
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._conn is not None:
            self._pool.putconn(self._conn)
        return False  # do not suppress exceptions


def ensure_tables() -> bool:
    """Create tables if they don't exist. Returns True on success."""
    try:
        with _PoolConn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS bot_state (
                        id INT PRIMARY KEY DEFAULT 1,
                        bankroll DOUBLE PRECISION NOT NULL DEFAULT 0,
                        total_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
                        today_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
                        open_positions JSONB NOT NULL DEFAULT '{}'
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS trades (
                        id SERIAL PRIMARY KEY,
                        ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        data JSONB NOT NULL
                    )
                """)
        log.info("PostgreSQL tables ready")
        return True
    except Exception as exc:
        log.warning(f"PostgreSQL unavailable — using file storage ({exc!r})")
        return False


def save_state(state: dict) -> None:
    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bot_state (id, bankroll, total_pnl, today_pnl, open_positions)
                VALUES (1, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    bankroll       = EXCLUDED.bankroll,
                    total_pnl      = EXCLUDED.total_pnl,
                    today_pnl      = EXCLUDED.today_pnl,
                    open_positions = EXCLUDED.open_positions
                """,
                (
                    state.get("bankroll", 0),
                    state.get("total_pnl", 0),
                    state.get("today_pnl", 0),
                    json.dumps(state.get("open_positions", {})),
                ),
            )


def load_state() -> dict:
    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT bankroll, total_pnl, today_pnl, open_positions "
                "FROM bot_state WHERE id = 1"
            )
            row = cur.fetchone()
    if not row:
        return {}
    open_pos = row[3] if isinstance(row[3], dict) else json.loads(row[3])
    return {
        "bankroll": float(row[0]),
        "total_pnl": float(row[1]),
        "today_pnl": float(row[2]),
        "open_positions": open_pos,
    }


def append_trade(trade: dict) -> None:
    ts = trade.get("timestamp", datetime.now(timezone.utc).isoformat())
    with _PoolConn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO trades (ts, data) VALUES (%s, %s)",
                (ts, json.dumps(trade)),
            )


def load_trades(days: int | None = None) -> list[dict]:
    with _PoolConn() as conn:
        with conn.cursor() as cur:
            if days is not None:
                cur.execute(
                    "SELECT data FROM trades "
                    "WHERE ts > NOW() - make_interval(days => %s) "
                    "ORDER BY ts",
                    (days,),
                )
            else:
                cur.execute("SELECT data FROM trades ORDER BY ts")
            rows = cur.fetchall()
    return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]
