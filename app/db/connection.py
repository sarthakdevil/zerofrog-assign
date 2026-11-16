import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("AGENT_DB_PATH", "data/agent.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    name TEXT NOT NULL,
    subject TEXT,
    value REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_name_ts ON metrics(name, timestamp);

CREATE TABLE IF NOT EXISTS process_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    pid INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    cpu_percent REAL NOT NULL,
    rss_bytes INTEGER,
    open_fds INTEGER
);
CREATE INDEX IF NOT EXISTS idx_process_samples_ts ON process_samples(timestamp);
CREATE INDEX IF NOT EXISTS idx_process_samples_pid_ts ON process_samples(pid, timestamp);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule TEXT NOT NULL,
    subject TEXT NOT NULL,
    opened_at REAL NOT NULL,
    closed_at REAL,
    peak_value REAL,
    details TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_open_unique
    ON alerts(rule, subject) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_alerts_closed_at ON alerts(closed_at);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


@contextmanager
def connection():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
    finally:
        conn.close()
