import os

from app.db import storage


def _float_env(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


CPU_HIGH_THRESHOLD = _float_env("CPU_HIGH_THRESHOLD", 85.0)
CPU_HIGH_DURATION_SECONDS = _float_env("CPU_HIGH_DURATION_SECONDS", 60.0)

RAM_HIGH_THRESHOLD = _float_env("RAM_HIGH_THRESHOLD", 90.0)
DISK_HIGH_THRESHOLD = _float_env("DISK_HIGH_THRESHOLD", 90.0)

ZOMBIE_DURATION_SECONDS = _float_env("ZOMBIE_DURATION_SECONDS", 30.0)

NETWORK_FAIL_STREAK = int(_float_env("NETWORK_FAIL_STREAK", 3))

MEMORY_CLIMB_SAMPLES = int(_float_env("MEMORY_CLIMB_SAMPLES", 10))
MEMORY_CLIMB_MIN_RISING_TRANSITIONS = int(_float_env("MEMORY_CLIMB_MIN_RISING_TRANSITIONS", 8))
MEMORY_CLIMB_MIN_RISE_BYTES = _float_env("MEMORY_CLIMB_MIN_RISE_BYTES", 50 * 1024 * 1024)
MEMORY_CLIMB_BUCKET_SECONDS = _float_env("MEMORY_CLIMB_BUCKET_SECONDS", 60.0)
MEMORY_CLIMB_CHECK_INTERVAL_SECONDS = _float_env("MEMORY_CLIMB_CHECK_INTERVAL_SECONDS", 60.0)


def _update(rule: str, subject: str, condition: bool, value: float, timestamp: float) -> None:
    if condition:
        storage.open_alert(rule, subject, value, timestamp)
    else:
        storage.close_alert(rule, subject, timestamp)


def _resample_buckets(history: list[dict], count: int, bucket_seconds: float) -> list[float]:
    buckets: dict[int, float] = {}
    for row in history:
        bucket = int(row["timestamp"] // bucket_seconds)
        buckets[bucket] = row["rss_bytes"] or 0
    ordered = [buckets[b] for b in sorted(buckets)]
    return ordered[-count:]


class RuleEngine:
    def __init__(self, watchlist: list[str] | None = None):
        self._watchlist = watchlist or []
        self._cpu_high_since: float | None = None
        self._zombie_since: dict[int, float] = {}
        self._network_fail_streak: dict[str, int] = {"gateway": 0, "internet": 0}
        self._watchlist_last_pid: dict[str, int] = {}
        self._last_memory_climb_check: float = 0.0
        self._memory_climbing_open: set[int] = set()

    def evaluate_sample(self, sample: dict) -> None:
        timestamp = sample["timestamp"]
        system = sample["system"]
        processes = sample["processes"]

        self._evaluate_cpu(system, timestamp)
        self._evaluate_ram(system, timestamp)
        self._evaluate_disks(system, timestamp)
        self._evaluate_zombies(processes, timestamp)
        self._evaluate_watchlist(processes, timestamp)

        if timestamp - self._last_memory_climb_check >= MEMORY_CLIMB_CHECK_INTERVAL_SECONDS:
            self._last_memory_climb_check = timestamp
            self._evaluate_memory_climbing(processes, timestamp)

    def _evaluate_cpu(self, system: dict, timestamp: float) -> None:
        cpu_total = system["cpu_percent_total"]
        if cpu_total > CPU_HIGH_THRESHOLD:
            if self._cpu_high_since is None:
                self._cpu_high_since = timestamp
        else:
            self._cpu_high_since = None

        sustained = (
            self._cpu_high_since is not None
            and timestamp - self._cpu_high_since >= CPU_HIGH_DURATION_SECONDS
        )
        _update("cpu_high", "cpu:total", sustained, cpu_total, timestamp)

    def _evaluate_ram(self, system: dict, timestamp: float) -> None:
        percent = system["ram"]["percent"]
        _update("ram_high", "ram", percent > RAM_HIGH_THRESHOLD, percent, timestamp)

    def _evaluate_disks(self, system: dict, timestamp: float) -> None:
        for disk in system["disks"]:
            _update(
                "disk_high",
                disk["mountpoint"],
                disk["percent"] > DISK_HIGH_THRESHOLD,
                disk["percent"],
                timestamp,
            )

    def _evaluate_zombies(self, processes: list[dict], timestamp: float) -> None:
        current_zombies = {p["pid"] for p in processes if p["status"] == "zombie"}

        for pid in current_zombies:
            self._zombie_since.setdefault(pid, timestamp)
            sustained = timestamp - self._zombie_since[pid] >= ZOMBIE_DURATION_SECONDS
            _update("zombie", f"pid:{pid}", sustained, pid, timestamp)

        for pid in [p for p in self._zombie_since if p not in current_zombies]:
            del self._zombie_since[pid]
            storage.close_alert("zombie", f"pid:{pid}", timestamp)

    def _evaluate_watchlist(self, processes: list[dict], timestamp: float) -> None:
        running_names = {p["name"]: p["pid"] for p in processes}

        for name in self._watchlist:
            if name in running_names:
                self._watchlist_last_pid[name] = running_names[name]
                storage.close_alert("process_died", name, timestamp)
            elif name in self._watchlist_last_pid:
                storage.open_alert("process_died", name, self._watchlist_last_pid[name], timestamp)

    def _evaluate_memory_climbing(self, processes: list[dict], timestamp: float) -> None:
        lookback_seconds = (MEMORY_CLIMB_SAMPLES + 1) * MEMORY_CLIMB_BUCKET_SECONDS
        current_pids = {p["pid"] for p in processes}

        for p in processes:
            pid = p["pid"]
            history = storage.get_process_history(pid, since=timestamp - lookback_seconds)
            samples = _resample_buckets(history, MEMORY_CLIMB_SAMPLES, MEMORY_CLIMB_BUCKET_SECONDS)

            if len(samples) < MEMORY_CLIMB_SAMPLES:
                continue

            rising_transitions = sum(1 for prev, curr in zip(samples, samples[1:]) if curr >= prev)
            total_rise = samples[-1] - samples[0]
            climbing = (
                rising_transitions >= MEMORY_CLIMB_MIN_RISING_TRANSITIONS
                and total_rise >= MEMORY_CLIMB_MIN_RISE_BYTES
            )
            _update("memory_climbing", f"pid:{pid}", climbing, total_rise, timestamp)
            if climbing:
                self._memory_climbing_open.add(pid)
            else:
                self._memory_climbing_open.discard(pid)

        for pid in [p for p in self._memory_climbing_open if p not in current_pids]:
            self._memory_climbing_open.discard(pid)
            storage.close_alert("memory_climbing", f"pid:{pid}", timestamp)

    def evaluate_probes(self, probe_result: dict) -> None:
        timestamp = probe_result["timestamp"]
        self._evaluate_network_target("gateway", probe_result["gateway"]["ok"], timestamp)
        self._evaluate_network_target("internet", probe_result["internet"]["ok"], timestamp)

    def _evaluate_network_target(self, subject: str, ok: bool, timestamp: float) -> None:
        if ok:
            self._network_fail_streak[subject] = 0
        else:
            self._network_fail_streak[subject] += 1

        lost = self._network_fail_streak[subject] >= NETWORK_FAIL_STREAK
        _update("network_lost", subject, lost, self._network_fail_streak[subject], timestamp)
