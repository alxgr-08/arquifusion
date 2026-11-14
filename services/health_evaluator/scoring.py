"""Health score engine (P1, P8). Pure functions: no AWS calls, no I/O.

Per metric: EWMA -> z-score vs own history and vs model fleet -> risk in the dangerous direction.
Machine: weighted risk -> health 0..100 -> severity, with persistence (streak) and a hard CRITICAL rule.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

SEVERITY_ORDER = {"OK": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}
EMIT_SEVERITIES = {"WARNING", "CRITICAL"}


class Direction(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"
    BOTH = "BOTH"


@dataclass(frozen=True)
class HealthConfig:
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "temperatureC": 0.30,
            "vibrationMmS": 0.30,
            "pressureBar": 0.15,
            "fuelRateLph": 0.15,
            "rpm": 0.10,
        }
    )
    directions: dict[str, Direction] = field(
        default_factory=lambda: {
            "temperatureC": Direction.HIGH,
            "vibrationMmS": Direction.HIGH,
            "pressureBar": Direction.LOW,
            "fuelRateLph": Direction.HIGH,
            "rpm": Direction.BOTH,
        }
    )
    watch_below: float = 80
    warning_below: float = 60
    critical_below: float = 40
    alpha: float = 0.2
    fleet_factor: float = 0.8
    max_risk: float = 5.0
    joint_risk: float = 2.0
    joint_multiplier: float = 1.25
    hard_critical_risk: float = 4.5
    streak_required: int = 3
    min_samples: int = 30
    min_std_ratio: float = 0.02
    signal_min_risk: float = 1.0
    learn_max_z: float = 3.0
    trend_ratio: float = 0.1

    @classmethod
    def from_params(
        cls, weights: dict[str, Any] | None = None, thresholds: dict[str, Any] | None = None
    ) -> HealthConfig:
        """Build from SSM /af/health/weights and /af/health/thresholds (JSON dicts). Unknown keys are ignored."""
        base = cls()
        overrides: dict[str, Any] = {}
        if weights:
            overrides["weights"] = {k: float(v) for k, v in weights.items()}
        for key, value in (thresholds or {}).items():
            if key in cls.__dataclass_fields__ and key not in ("weights", "directions"):
                overrides[key] = type(getattr(base, key))(value)
        return replace(base, **overrides)


@dataclass(frozen=True)
class MetricBaseline:
    """Own history of one metric. Welford running mean/variance + EWMA of recent readings."""

    mean: float = 0.0
    m2: float = 0.0
    n: int = 0
    ewma: float | None = None

    @property
    def std(self) -> float:
        return math.sqrt(self.m2 / (self.n - 1)) if self.n > 1 else 0.0

    def with_ewma(self, value: float, alpha: float) -> MetricBaseline:
        ewma = value if self.ewma is None else alpha * value + (1 - alpha) * self.ewma
        return replace(self, ewma=ewma)

    def with_sample(self, value: float) -> MetricBaseline:
        n = self.n + 1
        delta = value - self.mean
        mean = self.mean + delta / n
        return replace(self, n=n, mean=mean, m2=self.m2 + delta * (value - mean))

    def to_dict(self) -> dict[str, float | int | None]:
        return {"mean": self.mean, "m2": self.m2, "n": self.n, "ewma": self.ewma, "std": self.std}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetricBaseline:
        ewma = data.get("ewma")
        return cls(
            mean=float(data.get("mean", 0)),
            m2=float(data.get("m2", 0)),
            n=int(data.get("n", 0)),
            ewma=None if ewma is None else float(ewma),
        )


@dataclass(frozen=True)
class FleetBaseline:
    """Baseline of a machine model (MODEL#{model} BASELINE), computed in BigQuery."""

    mean: float
    std: float


@dataclass(frozen=True)
class MachineState:
    baselines: dict[str, MetricBaseline] = field(default_factory=dict)
    severity: str = "OK"
    anomaly_streak: int = 0


@dataclass(frozen=True)
class Signal:
    metric: str
    value: float
    baseline: float
    z_score: float
    trend: str
    risk: float

    def to_contract(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": round(self.value, 3),
            "baseline": round(self.baseline, 3),
            "zScore": round(self.z_score, 2),
            "trend": self.trend,
        }


@dataclass(frozen=True)
class Evaluation:
    health_score: float
    severity: str
    raw_severity: str
    signals: list[Signal]
    compared_against: list[str]
    state: MachineState
    should_emit: bool


def _effective_std(mean: float, std: float, config: HealthConfig) -> float:
    return max(std, abs(mean) * config.min_std_ratio, 1e-6)


def _directional(z: float, direction: Direction) -> float:
    if direction is Direction.HIGH:
        return z
    if direction is Direction.LOW:
        return -z
    return abs(z)


def _trend(previous_ewma: float | None, ewma: float, std: float, config: HealthConfig) -> str:
    if previous_ewma is None:
        return "STABLE"
    delta = ewma - previous_ewma
    if delta > config.trend_ratio * std:
        return "RISING"
    if delta < -config.trend_ratio * std:
        return "FALLING"
    return "STABLE"


def _is_learnable(value: float, previous: MetricBaseline | None, risk: float, config: HealthConfig) -> bool:
    if previous is None or previous.n < config.min_samples:
        return True  # warm-up: learn everything
    if risk >= config.signal_min_risk:
        return False
    std = _effective_std(previous.mean, previous.std, config)
    return abs(value - previous.mean) / std <= config.learn_max_z


def severity_from_health(health: float, config: HealthConfig) -> str:
    if health < config.critical_below:
        return "CRITICAL"
    if health < config.warning_below:
        return "WARNING"
    if health < config.watch_below:
        return "WATCH"
    return "OK"


def _confirm_severity(raw: str, state: MachineState, hard_critical: bool, config: HealthConfig) -> tuple[str, int]:
    """Severity rises only after `streak_required` consecutive readings (or immediately on the hard rule).
    It drops immediately when readings improve."""
    if hard_critical:
        return "CRITICAL", 0
    if SEVERITY_ORDER[raw] <= SEVERITY_ORDER[state.severity]:
        return raw, 0
    streak = state.anomaly_streak + 1
    if streak >= config.streak_required:
        return raw, 0
    return state.severity, streak


def evaluate(
    metrics: dict[str, float | None],
    state: MachineState,
    fleet: dict[str, FleetBaseline] | None = None,
    status: str = "OPERATING",
    config: HealthConfig | None = None,
) -> Evaluation:
    """Evaluate one telemetry reading. Returns the score, severity, signals and the next machine state."""
    config = config or HealthConfig()
    fleet = fleet or {}
    baselines = dict(state.baselines)
    risks: dict[str, float] = {}
    signals: list[Signal] = []
    used: set[str] = set()
    observed: dict[str, float] = {}

    for metric, weight in config.weights.items():
        value = metrics.get(metric)
        if value is None or weight <= 0:
            continue
        observed[metric] = float(value)
        direction = config.directions.get(metric, Direction.HIGH)
        previous = baselines.get(metric, MetricBaseline())
        current = previous.with_ewma(float(value), config.alpha)
        baselines[metric] = current

        references: list[tuple[str, float, float, float]] = []  # (source, mean, std, factor)
        if previous.n >= config.min_samples:
            references.append(("OWN_HISTORY", previous.mean, previous.std, 1.0))
        if (model := fleet.get(metric)) is not None:
            references.append(("MODEL_FLEET", model.mean, model.std, config.fleet_factor))
        if not references:
            continue

        scored = []
        for source, mean, std, factor in references:
            eff_std = _effective_std(mean, std, config)
            z = _directional((current.ewma - mean) / eff_std, direction)
            scored.append((factor * z, z, mean, eff_std))
            used.add(source)
        weighted_z, raw_z, reference, ref_std = max(scored, key=lambda s: s[0])
        risk = min(max(weighted_z, 0.0), config.max_risk)
        risks[metric] = risk
        trend = _trend(previous.ewma, current.ewma, ref_std, config)
        signals.append(Signal(metric, float(value), reference, raw_z, trend, risk))

    if risks:
        total_weight = sum(config.weights[m] for m in risks)
        weighted = sum(config.weights[m] * r for m, r in risks.items()) / total_weight
        if sum(1 for r in risks.values() if r > config.joint_risk) >= 2:
            weighted *= config.joint_multiplier
        health = max(0.0, min(100.0, 100 - 20 * weighted))
    else:
        health = 100.0

    hard_critical = any(r >= config.hard_critical_risk for r in risks.values())
    raw_severity = "CRITICAL" if hard_critical else severity_from_health(health, config)
    severity, streak = _confirm_severity(raw_severity, state, hard_critical, config)

    # Learn only from healthy, operating readings so a developing fault is never learned as normal.
    # raw_severity (not the confirmed one): readings inside a pending streak are already suspicious.
    # Per-metric gate: a single drifting metric can be diluted by the weighted average, so outliers are never learned.
    if raw_severity == "OK" and status == "OPERATING":
        for metric, value in observed.items():
            if _is_learnable(value, state.baselines.get(metric), risks.get(metric, 0.0), config):
                baselines[metric] = baselines[metric].with_sample(value)

    should_emit = severity in EMIT_SEVERITIES and SEVERITY_ORDER[severity] > SEVERITY_ORDER[state.severity]
    signals.sort(key=lambda s: s.risk, reverse=True)
    relevant = [s for s in signals if s.risk >= config.signal_min_risk] or signals[:1]

    return Evaluation(
        health_score=round(health, 1),
        severity=severity,
        raw_severity=raw_severity,
        signals=relevant,
        compared_against=sorted(used),
        state=MachineState(baselines=baselines, severity=severity, anomaly_streak=streak),
        should_emit=should_emit,
    )
