"""PostgreSQL persistence layer. Only active when DATABASE_URL env var is set."""
import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_conn = None


def _get_conn():
    global _conn
    if _conn is None or getattr(_conn, "closed", 1):
        import psycopg2
        url = os.getenv("DATABASE_URL", "")
        if not url:
            raise RuntimeError("DATABASE_URL not set")
        _conn = psycopg2.connect(url, connect_timeout=5)
        _conn.autocommit = True
    return _conn


def ensure_tables() -> bool:
    """Create tables if they don't exist. Returns True on success."""
    try:
        conn = _get_conn()
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
    conn = _get_conn()
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
    conn = _get_conn()
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
    conn = _get_conn()
    ts = trade.get("timestamp", datetime.now(timezone.utc).isoformat())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trades (ts, data) VALUES (%s, %s)",
            (ts, json.dumps(trade)),
        )


def load_trades(days: int | None = None) -> list[dict]:
    conn = _get_conn()
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
