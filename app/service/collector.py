import os
import time

import psutil

_IS_POSIX = os.name == "posix"
_PROCESS_ATTRS = ["pid", "name", "status", "cpu_percent", "memory_info"]
if _IS_POSIX:
    _PROCESS_ATTRS.append("num_fds")


def _rate(current: int, previous: int, elapsed: float) -> float:
    if elapsed <= 0:
        return 0.0
    delta = current - previous
    if delta < 0:
        delta = current
    return delta / elapsed


class SystemCollector:
    def __init__(self):
        self._last_disk_io = psutil.disk_io_counters()
        self._last_net_io = psutil.net_io_counters(pernic=True)
        self._last_ts = time.monotonic()
        psutil.cpu_percent(percpu=True)

    def collect(self) -> dict:
        now = time.monotonic()
        elapsed = now - self._last_ts
        self._last_ts = now

        data = {
            "timestamp": time.time(),
            "system": self._collect_system(elapsed),
            "network": self._collect_network(elapsed),
            "processes": self._collect_processes(),
        }
        return data

    def _collect_system(self, elapsed: float) -> dict:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()

        disks = []
        for part in psutil.disk_partitions(all=True):
            if not os.path.isdir(part.mountpoint):
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            disks.append(
                {
                    "mountpoint": part.mountpoint,
                    "device": part.device,
                    "fstype": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "percent": usage.percent,
                }
            )

        current_disk_io = psutil.disk_io_counters()
        disk_io = {
            "read_bytes_per_sec": _rate(current_disk_io.read_bytes, self._last_disk_io.read_bytes, elapsed),
            "write_bytes_per_sec": _rate(current_disk_io.write_bytes, self._last_disk_io.write_bytes, elapsed),
            "read_count_per_sec": _rate(current_disk_io.read_count, self._last_disk_io.read_count, elapsed),
            "write_count_per_sec": _rate(current_disk_io.write_count, self._last_disk_io.write_count, elapsed),
        }
        self._last_disk_io = current_disk_io

        try:
            load1, load5, load15 = os.getloadavg()
        except (AttributeError, OSError):
            load1, load5, load15 = None, None, None

        return {
            "cpu_percent_per_core": psutil.cpu_percent(percpu=True),
            "cpu_percent_total": psutil.cpu_percent(),
            "ram": {"total": vm.total, "used": vm.used, "percent": vm.percent},
            "swap": {"total": swap.total, "used": swap.used, "percent": swap.percent},
            "disks": disks,
            "disk_io": disk_io,
            "uptime_seconds": time.time() - psutil.boot_time(),
            "load_average": {"1m": load1, "5m": load5, "15m": load15},
        }

    def _collect_network(self, elapsed: float) -> dict:
        current_net_io = psutil.net_io_counters(pernic=True)
        interfaces = {}
        for name, counters in current_net_io.items():
            prev = self._last_net_io.get(name, counters)
            interfaces[name] = {
                "bytes_sent_per_sec": _rate(counters.bytes_sent, prev.bytes_sent, elapsed),
                "bytes_recv_per_sec": _rate(counters.bytes_recv, prev.bytes_recv, elapsed),
                "packets_sent_per_sec": _rate(counters.packets_sent, prev.packets_sent, elapsed),
                "packets_recv_per_sec": _rate(counters.packets_recv, prev.packets_recv, elapsed),
                "errin": counters.errin,
                "errout": counters.errout,
                "dropin": counters.dropin,
                "dropout": counters.dropout,
            }
        self._last_net_io = current_net_io

        try:
            connections = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            connections = []

        tcp_connections = sum(1 for c in connections if c.type.name == "SOCK_STREAM")
        listening_ports = sorted(
            {c.laddr.port for c in connections if c.status == psutil.CONN_LISTEN}
        )

        return {
            "interfaces": interfaces,
            "tcp_connections": tcp_connections,
            "listening_ports": listening_ports,
        }

    def _collect_processes(self) -> list[dict]:
        processes = []
        for proc in psutil.process_iter(attrs=_PROCESS_ATTRS):
            try:
                info = proc.info
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

            mem_info = info.get("memory_info")
            processes.append(
                {
                    "pid": info["pid"],
                    "name": info["name"],
                    "status": info["status"],
                    "cpu_percent": info["cpu_percent"],
                    "rss_bytes": mem_info.rss if mem_info else None,
                    "open_fds": info.get("num_fds"),
                }
            )
        return processes
