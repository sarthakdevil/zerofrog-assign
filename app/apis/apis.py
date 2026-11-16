from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.db import storage

app = FastAPI()

RULE_SEVERITY = {
    "cpu_high": "critical",
    "ram_high": "critical",
    "disk_high": "critical",
    "network_lost": "critical",
    "process_died": "critical",
    "zombie": "warn",
    "memory_climbing": "warn",
}

DASHBOARD_PATH = Path(__file__).parent / "static" / "dashboard.html"


@app.get("/")
def dashboard():
    return FileResponse(DASHBOARD_PATH)


@app.get("/health")
def health():
    open_alerts = storage.get_open_alerts()
    severities = {RULE_SEVERITY.get(a["rule"], "warn") for a in open_alerts}

    if not open_alerts:
        status = "ok"
    elif "critical" in severities:
        status = "critical"
    else:
        status = "warn"

    return {"status": status, "open_alerts": open_alerts}


@app.get("/metrics")
def metrics(name: str, since: float | None = None):
    return storage.query_metrics(name, since=since)


@app.get("/alerts")
def alerts(open: bool | None = None):
    return storage.get_alerts(open=open)


@app.get("/processes")
def processes(sort: str = "cpu"):
    return storage.get_latest_processes(sort=sort)
