import time

from .connection import connection

_PROCESS_SORT_COLUMNS = {
    "cpu": "cpu_percent",
    "ram": "rss_bytes",
    "fds": "open_fds",
}


def insert_metrics(timestamp: float, rows: list[tuple[str, str | None, float]]) -> None:
    with connection() as conn:
        conn.executemany(
            "INSERT INTO metrics (timestamp, name, subject, value) VALUES (?, ?, ?, ?)",
            [(timestamp, name, subject, value) for name, subject, value in rows],
        )


def query_metrics(name: str, since: float | None = None, subject: str | None = None) -> list[dict]:
    query = "SELECT timestamp, name, subject, value FROM metrics WHERE name = ?"
    params: list = [name]
    if subject is not None:
        query += " AND subject = ?"
        params.append(subject)
    if since is not None:
        query += " AND timestamp >= ?"
        params.append(since)
    query += " ORDER BY timestamp ASC"

    with connection() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def insert_process_samples(timestamp: float, processes: list[dict]) -> None:
    with connection() as conn:
        conn.executemany(
            "INSERT INTO process_samples "
            "(timestamp, pid, name, status, cpu_percent, rss_bytes, open_fds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    timestamp,
                    p["pid"],
                    p["name"],
                    p["status"],
                    p["cpu_percent"],
                    p["rss_bytes"],
                    p["open_fds"],
                )
                for p in processes
            ],
        )


def get_process_history(pid: int, since: float) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT timestamp, rss_bytes, cpu_percent, status FROM process_samples "
            "WHERE pid = ? AND timestamp >= ? ORDER BY timestamp ASC",
            (pid, since),
        ).fetchall()
        return [dict(row) for row in rows]


def get_latest_processes(sort: str = "cpu") -> list[dict]:
    column = _PROCESS_SORT_COLUMNS.get(sort, "cpu_percent")
    query = f"""
        SELECT pid, name, status, cpu_percent, rss_bytes, open_fds, timestamp
        FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY pid ORDER BY timestamp DESC) AS rn
            FROM process_samples
        )
        WHERE rn = 1
        ORDER BY {column} DESC
    """
    with connection() as conn:
        return [dict(row) for row in conn.execute(query).fetchall()]


def open_alert(rule: str, subject: str, value: float, timestamp: float) -> int:
    with connection() as conn:
        row = conn.execute(
            "SELECT id, peak_value FROM alerts WHERE rule = ? AND subject = ? AND closed_at IS NULL",
            (rule, subject),
        ).fetchone()

        if row is not None:
            if row["peak_value"] is None or value > row["peak_value"]:
                conn.execute("UPDATE alerts SET peak_value = ? WHERE id = ?", (value, row["id"]))
            return row["id"]

        cursor = conn.execute(
            "INSERT INTO alerts (rule, subject, opened_at, closed_at, peak_value) "
            "VALUES (?, ?, ?, NULL, ?)",
            (rule, subject, timestamp, value),
        )
        return cursor.lastrowid


def close_alert(rule: str, subject: str, timestamp: float) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE alerts SET closed_at = ? WHERE rule = ? AND subject = ? AND closed_at IS NULL",
            (timestamp, rule, subject),
        )


def close_all_open_alerts(timestamp: float) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE alerts SET closed_at = ? WHERE closed_at IS NULL",
            (timestamp,),
        )


def get_open_alerts() -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE closed_at IS NULL ORDER BY opened_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_alerts(open: bool | None = None) -> list[dict]:
    query = "SELECT * FROM alerts"
    if open is True:
        query += " WHERE closed_at IS NULL"
    elif open is False:
        query += " WHERE closed_at IS NOT NULL"
    query += " ORDER BY opened_at DESC"

    with connection() as conn:
        return [dict(row) for row in conn.execute(query).fetchall()]


def prune_old(retention_days: int = 7, vacuum: bool = False) -> None:
    cutoff = time.time() - retention_days * 86400
    with connection() as conn:
        conn.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
        conn.execute("DELETE FROM process_samples WHERE timestamp < ?", (cutoff,))
        conn.execute("DELETE FROM alerts WHERE closed_at IS NOT NULL AND closed_at < ?", (cutoff,))

    if vacuum:
        with connection() as conn:
            conn.execute("VACUUM")
