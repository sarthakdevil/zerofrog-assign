import re
import socket
import struct
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

import dns.resolver
import httpx

_LOSS_RE = re.compile(r"(\d+(?:\.\d+)?)% packet loss")


def get_default_gateway() -> str | None:
    try:
        with open("/proc/net/route") as f:
            lines = f.readlines()[1:]
    except (FileNotFoundError, OSError):
        return None

    for line in lines:
        fields = line.strip().split()
        if len(fields) < 3:
            continue
        _iface, destination, gateway = fields[0], fields[1], fields[2]
        if destination == "00000000":
            return socket.inet_ntoa(struct.pack("<L", int(gateway, 16)))
    return None


def measure_dns_resolution_time(hostname: str, timeout: float = 2.0) -> dict:
    resolver = dns.resolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = timeout
    start = time.monotonic()
    try:
        resolver.resolve(hostname)
        return {"ok": True, "resolution_time_ms": (time.monotonic() - start) * 1000, "error": None}
    except Exception as exc:
        return {"ok": False, "resolution_time_ms": (time.monotonic() - start) * 1000, "error": str(exc)}


def measure_tcp_connect_time(host: str, port: int, timeout: float = 2.0) -> dict:
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return {"ok": True, "connect_time_ms": (time.monotonic() - start) * 1000, "error": None}
    except OSError as exc:
        return {"ok": False, "connect_time_ms": (time.monotonic() - start) * 1000, "error": str(exc)}


def check_packet_loss(host: str, count: int = 4, timeout: float = 2.0) -> dict:
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(max(1, int(timeout))), host],
            capture_output=True,
            text=True,
            timeout=timeout * count + 5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {"ok": False, "packet_loss_pct": 100.0, "error": str(exc)}

    match = _LOSS_RE.search(result.stdout)
    if not match:
        return {"ok": False, "packet_loss_pct": 100.0, "error": "could not parse ping output"}

    loss_pct = float(match.group(1))
    return {"ok": loss_pct < 100.0, "packet_loss_pct": loss_pct, "error": None}


def check_gateway_reachable(timeout: float = 2.0) -> dict:
    gateway = get_default_gateway()
    if gateway is None:
        return {"ok": False, "gateway": None, "packet_loss_pct": 100.0, "error": "default gateway not found"}

    loss = check_packet_loss(gateway, count=2, timeout=timeout)
    return {"ok": loss["ok"], "gateway": gateway, "packet_loss_pct": loss["packet_loss_pct"], "error": loss["error"]}


def probe_api_endpoint(url: str, timeout: float = 2.0) -> dict:
    start = time.monotonic()
    try:
        response = httpx.get(url, timeout=timeout)
        return {
            "ok": response.is_success,
            "status_code": response.status_code,
            "response_time_ms": (time.monotonic() - start) * 1000,
            "error": None,
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "status_code": None,
            "response_time_ms": (time.monotonic() - start) * 1000,
            "error": str(exc),
        }


def _run_with_hard_timeout(func, timeout: float, *args, **kwargs) -> dict:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout + 1)
        except FutureTimeoutError:
            return {"ok": False, "error": "probe timed out", "timed_out": True}


def run_probes(
    dns_host: str,
    tcp_host: str,
    tcp_port: int,
    internet_host: str,
    api_url: str,
    timeout: float = 2.0,
) -> dict:
    gateway_ping_count = 2
    internet_ping_count = 4

    return {
        "timestamp": time.time(),
        "dns": _run_with_hard_timeout(measure_dns_resolution_time, timeout + 1, dns_host, timeout),
        "tcp_connect": _run_with_hard_timeout(measure_tcp_connect_time, timeout + 1, tcp_host, tcp_port, timeout),
        "gateway": _run_with_hard_timeout(
            check_gateway_reachable, timeout * gateway_ping_count + 6, timeout
        ),
        "internet": _run_with_hard_timeout(
            check_packet_loss, timeout * internet_ping_count + 6, internet_host, internet_ping_count, timeout
        ),
        "api_endpoint": _run_with_hard_timeout(probe_api_endpoint, timeout + 1, api_url, timeout),
    }
