from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable


_METRIC_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class JsonLogger:
    """Small JSON-lines logger suitable for containers and log collectors."""

    _LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}

    def __init__(self, level: str = "info", stream=None) -> None:
        normalized = level.lower()
        self.level = normalized if normalized in self._LEVELS else "info"
        self.stream = stream or sys.stderr
        self._lock = threading.Lock()

    def log(self, level: str, message: str, **fields: Any) -> None:
        normalized = level.lower()
        if self._LEVELS.get(normalized, 20) < self._LEVELS[self.level]:
            return
        record = {
            "timestamp": utc_now(),
            "level": normalized,
            "message": message,
            **fields,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.stream.write(line + "\n")
            self.stream.flush()

    def info(self, message: str, **fields: Any) -> None:
        self.log("info", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self.log("warning", message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self.log("error", message, **fields)


LabelSet = tuple[tuple[str, str], ...]


def _normalize_labels(labels: dict[str, str] | None) -> LabelSet:
    if not labels:
        return ()
    normalized = []
    for name, value in labels.items():
        if not _LABEL_NAME.match(name):
            raise ValueError(f"invalid metric label: {name}")
        normalized.append((name, str(value)))
    return tuple(sorted(normalized))


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class MetricsRegistry:
    """Thread-safe in-process counters and gauges with Prometheus text output."""

    def __init__(self, namespace: str = "wms") -> None:
        self.namespace = namespace
        self._counters: dict[tuple[str, LabelSet], float] = defaultdict(float)
        self._gauges: dict[tuple[str, LabelSet], float] = {}
        self._lock = threading.Lock()
        self._started_at = time.time()

    def _name(self, name: str) -> str:
        metric = f"{self.namespace}_{name}" if self.namespace else name
        if not _METRIC_NAME.match(metric):
            raise ValueError(f"invalid metric name: {metric}")
        return metric

    def inc(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        amount: float = 1,
    ) -> None:
        key = (self._name(name), _normalize_labels(labels))
        with self._lock:
            self._counters[key] += float(amount)

    def set_gauge(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        key = (self._name(name), _normalize_labels(labels))
        with self._lock:
            self._gauges[key] = float(value)

    def render(self) -> str:
        self.set_gauge("process_uptime_seconds", max(0.0, time.time() - self._started_at))
        with self._lock:
            counters = sorted(self._counters.items())
            gauges = sorted(self._gauges.items())
        lines = self._render_family("gauge", gauges)
        lines.extend(self._render_family("counter", counters))
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_sample(name: str, labels: LabelSet, value: float) -> str:
        if labels:
            label_text = ",".join(
                f'{label}="{_escape_label(label_value)}"'
                for label, label_value in labels
            )
            return f"{name}{{{label_text}}} {value:g}"
        return f"{name} {value:g}"

    def _render_family(
        self,
        metric_type: str,
        samples: list[tuple[tuple[str, LabelSet], float]],
    ) -> list[str]:
        grouped: dict[str, list[tuple[LabelSet, float]]] = defaultdict(list)
        for (name, labels), value in samples:
            grouped[name].append((labels, value))
        lines = []
        for name in sorted(grouped):
            lines.append(f"# TYPE {name} {metric_type}")
            for labels, value in grouped[name]:
                lines.append(self._render_sample(name, labels, value))
        return lines


def collect_service_metrics(service: Any, metrics: MetricsRegistry) -> None:
    """Project current business state into bounded-cardinality gauges."""

    snapshot = service.snapshot()
    tasks = snapshot["tasks"]
    agvs = snapshot["agvs"]
    runtime = snapshot["runtime"]
    counters = runtime.get("counters", {})

    metrics.set_gauge("tick", float(service.tick))
    metrics.set_gauge("orders", float(len(snapshot["orders"])))
    metrics.set_gauge("tasks", float(len(tasks)))
    metrics.set_gauge("agvs", float(len(agvs)))
    metrics.set_gauge(
        "tasks_active",
        float(sum(task["status"] in {"pending", "assigned", "running"} for task in tasks)),
    )
    metrics.set_gauge(
        "tasks_completed",
        float(sum(task["status"] == "completed" for task in tasks)),
    )
    metrics.set_gauge(
        "agvs_idle",
        float(sum(agv["status"] == "idle" for agv in agvs)),
    )
    metrics.set_gauge(
        "agvs_charging",
        float(sum(agv["status"] == "charging" for agv in agvs)),
    )
    metrics.set_gauge(
        "agvs_fault",
        float(sum(agv["status"] in {"fault", "offline"} for agv in agvs)),
    )
    for name in (
        "conflicts_prevented",
        "deadlocks_detected",
        "deadlocks_resolved",
        "tasks_reassigned",
    ):
        metrics.set_gauge(f"business_{name}", float(counters.get(name, 0)))
    metrics.set_gauge(
        "simulation_paused",
        1.0 if runtime.get("paused", True) else 0.0,
    )


def prometheus_lines(samples: Iterable[tuple[str, dict[str, str], float]]) -> str:
    """Render ad-hoc samples, primarily useful in tests."""

    lines = []
    for name, labels, value in samples:
        normalized = _normalize_labels(labels)
        lines.append(MetricsRegistry._render_sample(name, normalized, value))
    return "\n".join(lines)
