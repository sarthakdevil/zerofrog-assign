import asyncio
import time
import tomllib
from pathlib import Path

import uvicorn

from app.apis.apis import app
from app.db import connection, storage
from app.service.collector import SystemCollector
from app.service.probes import run_probes
from app.utils.rules import RuleEngine

CONFIG_PATH = Path("config.toml")


def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def metric_rows_from_sample(sample: dict) -> list[tuple[str, str | None, float]]:
    system = sample["system"]
    rows: list[tuple[str, str | None, float]] = []

    for i, pct in enumerate(system["cpu_percent_per_core"]):
        rows.append(("cpu.core", str(i), pct))
    rows.append(("cpu.total", None, system["cpu_percent_total"]))
    rows.append(("ram.percent", None, system["ram"]["percent"]))
    rows.append(("swap.percent", None, system["swap"]["percent"]))

    for disk in system["disks"]:
        rows.append(("disk.percent", disk["mountpoint"], disk["percent"]))

    rows.append(("disk_io.read_bytes_per_sec", None, system["disk_io"]["read_bytes_per_sec"]))
    rows.append(("disk_io.write_bytes_per_sec", None, system["disk_io"]["write_bytes_per_sec"]))

    network = sample["network"]
    for iface, counters in network["interfaces"].items():
        rows.append(("net.bytes_sent_per_sec", iface, counters["bytes_sent_per_sec"]))
        rows.append(("net.bytes_recv_per_sec", iface, counters["bytes_recv_per_sec"]))
    rows.append(("net.tcp_connections", None, network["tcp_connections"]))

    return rows


def probe_rows_from_result(result: dict) -> list[tuple[str, str | None, float]]:
    rows: list[tuple[str, str | None, float]] = []

    if result["dns"].get("resolution_time_ms") is not None:
        rows.append(("probe.dns_resolution_ms", None, result["dns"]["resolution_time_ms"]))
    if result["tcp_connect"].get("connect_time_ms") is not None:
        rows.append(("probe.tcp_connect_ms", None, result["tcp_connect"]["connect_time_ms"]))
    if result["gateway"].get("packet_loss_pct") is not None:
        rows.append(("probe.gateway_packet_loss_pct", None, result["gateway"]["packet_loss_pct"]))
    if result["internet"].get("packet_loss_pct") is not None:
        rows.append(("probe.internet_packet_loss_pct", None, result["internet"]["packet_loss_pct"]))
    if result["api_endpoint"].get("response_time_ms") is not None:
        rows.append(("probe.api_response_ms", None, result["api_endpoint"]["response_time_ms"]))

    return rows


async def collector_loop(collector: SystemCollector, rules: RuleEngine, interval: float) -> None:
    loop = asyncio.get_running_loop()
    while True:
        sample = await loop.run_in_executor(None, collector.collect)
        storage.insert_metrics(sample["timestamp"], metric_rows_from_sample(sample))
        storage.insert_process_samples(sample["timestamp"], sample["processes"])
        rules.evaluate_sample(sample)
        await asyncio.sleep(interval)


async def probe_loop(rules: RuleEngine, network_config: dict, interval: float) -> None:
    loop = asyncio.get_running_loop()
    while True:
        result = await loop.run_in_executor(
            None,
            run_probes,
            network_config["dns_host"],
            network_config["tcp_host"],
            network_config["tcp_port"],
            network_config["internet_host"],
            network_config["api_url"],
            network_config.get("timeout", 2.0),
        )
        storage.insert_metrics(result["timestamp"], probe_rows_from_result(result))
        rules.evaluate_probes(result)
        await asyncio.sleep(interval)


async def retention_loop(retention_days: int, interval: float) -> None:
    while True:
        storage.prune_old(retention_days=retention_days, vacuum=True)
        await asyncio.sleep(interval)


async def run() -> None:
    config = load_config()
    connection.init_db()
    storage.close_all_open_alerts(time.time())

    collector = SystemCollector()
    rules = RuleEngine(watchlist=config.get("watchlist", {}).get("processes", []))

    server_config = config.get("server", {})
    network_config = config.get("network", {})
    retention_days = config.get("retention", {}).get("days", 7)

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=server_config.get("host", "0.0.0.0"),
            port=server_config.get("port", 8000),
            log_level="info",
        )
    )

    await asyncio.gather(
        server.serve(),
        collector_loop(collector, rules, interval=5.0),
        probe_loop(rules, network_config, interval=15.0),
        retention_loop(retention_days, interval=86400.0),
    )


if __name__ == "__main__":
    asyncio.run(run())
