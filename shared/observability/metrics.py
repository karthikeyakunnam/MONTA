"""
MONTA — Metrics Registry
==========================
Thread- and asyncio-safe counters, gauges and histograms with Prometheus
text exposition (``render_prometheus``). Dependency-free so every service can
expose ``/metrics`` identically; a Prometheus server scrapes it.

Label cardinality is bounded by construction: only enum-like labels
(provider, model, agent, rule, pattern, outcome) are allowed — never ids.
"""

import math
import threading
from collections.abc import Sequence

DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
SCORE_BUCKETS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
RATIO_BUCKETS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0)


def _key(labelnames: Sequence[str], labels: dict[str, str]) -> tuple[str, ...]:
    missing = set(labelnames) - set(labels)
    extra = set(labels) - set(labelnames)
    if missing or extra:
        raise ValueError(f"labels mismatch: missing {sorted(missing)}, unexpected {sorted(extra)}")
    return tuple(str(labels[n]) for n in labelnames)


class _Metric:
    kind = ""

    def __init__(self, name: str, documentation: str, labelnames: Sequence[str] = ()):
        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames)
        self._lock = threading.Lock()


class Counter(_Metric):
    kind = "counter"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._values: dict[tuple[str, ...], float] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError("counters only increase")
        k = _key(self.labelnames, labels)
        with self._lock:
            self._values[k] = self._values.get(k, 0.0) + amount

    def value(self, **labels: str) -> float:
        return self._values.get(_key(self.labelnames, labels), 0.0)

    def samples(self):
        with self._lock:
            return [(self.name, k, v) for k, v in self._values.items()]


class Gauge(_Metric):
    kind = "gauge"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._values: dict[tuple[str, ...], float] = {}

    def set(self, value: float, **labels: str) -> None:
        with self._lock:
            self._values[_key(self.labelnames, labels)] = float(value)

    def value(self, **labels: str) -> float:
        return self._values.get(_key(self.labelnames, labels), 0.0)

    def samples(self):
        with self._lock:
            return [(self.name, k, v) for k, v in self._values.items()]


class Histogram(_Metric):
    kind = "histogram"

    def __init__(self, name, documentation, labelnames=(), buckets: Sequence[float] = DEFAULT_BUCKETS):
        super().__init__(name, documentation, labelnames)
        self.buckets = tuple(sorted(buckets)) + (math.inf,)
        self._counts: dict[tuple[str, ...], list[int]] = {}
        self._sums: dict[tuple[str, ...], float] = {}

    def observe(self, value: float, **labels: str) -> None:
        k = _key(self.labelnames, labels)
        with self._lock:
            counts = self._counts.setdefault(k, [0] * len(self.buckets))
            for i, b in enumerate(self.buckets):
                if value <= b:
                    counts[i] += 1
            self._sums[k] = self._sums.get(k, 0.0) + value

    def count(self, **labels: str) -> int:
        c = self._counts.get(_key(self.labelnames, labels))
        return c[-1] if c else 0

    def sum(self, **labels: str) -> float:
        return self._sums.get(_key(self.labelnames, labels), 0.0)

    def samples(self):
        out = []
        with self._lock:
            for k, counts in self._counts.items():
                for b, c in zip(self.buckets, counts):
                    out.append((f"{self.name}_bucket", k + ("+Inf" if math.isinf(b) else repr(b),), c))
                out.append((f"{self.name}_count", k, counts[-1]))
                out.append((f"{self.name}_sum", k, self._sums[k]))
        return out


class Registry:
    def __init__(self):
        self._metrics: dict[str, _Metric] = {}

    def register(self, metric: _Metric) -> _Metric:
        if metric.name in self._metrics:
            raise ValueError(f"metric {metric.name} already registered")
        self._metrics[metric.name] = metric
        return metric

    def counter(self, name, doc, labels=()) -> Counter:
        return self.register(Counter(name, doc, labels))

    def gauge(self, name, doc, labels=()) -> Gauge:
        return self.register(Gauge(name, doc, labels))

    def histogram(self, name, doc, labels=(), buckets=DEFAULT_BUCKETS) -> Histogram:
        return self.register(Histogram(name, doc, labels, buckets))

    def get(self, name: str) -> _Metric:
        return self._metrics[name]

    def __iter__(self):
        return iter(self._metrics.values())

    def render_prometheus(self) -> str:
        lines: list[str] = []
        for m in self._metrics.values():
            lines.append(f"# HELP {m.name} {m.documentation}")
            lines.append(f"# TYPE {m.name} {m.kind}")
            for sample_name, key, value in m.samples():
                names = list(m.labelnames) + (["le"] if sample_name.endswith("_bucket") else [])
                label_str = ",".join(f'{n}="{_escape(v)}"' for n, v in zip(names, key))
                lines.append(f"{sample_name}{{{label_str}}} {value}" if label_str else f"{sample_name} {value}")
        return "\n".join(lines) + "\n"


def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


REGISTRY = Registry()
