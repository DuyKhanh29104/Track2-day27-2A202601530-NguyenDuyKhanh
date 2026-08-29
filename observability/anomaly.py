"""Statistical anomaly detection for reliability metrics.

The explicit ``zscore`` and ``mad`` methods are kept for comparison in the
lab. ``auto`` uses a robust median/MAD baseline and prefers a seasonal segment
provided through ``context['same_segment_history']``. This prevents a normal
weekend value from being compared with weekday traffic while remaining fully
deterministic and model-free.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def _finite_values(values: Iterable[float] | None) -> np.ndarray:
    """Convert an iterable to finite floats, ignoring malformed observations."""
    if values is None:
        return np.asarray([], dtype=float)

    cleaned: list[float] = []
    for value in values:
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if np.isfinite(numeric):
            cleaned.append(numeric)
    return np.asarray(cleaned, dtype=float)


def _current_value(current: float) -> float | None:
    try:
        value = float(current)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if np.isfinite(value) else None


def _invalid_current_result(method: str) -> dict[str, Any]:
    return {
        "is_anomaly": True,
        "score": float("inf"),
        "method": method,
        "reason": "current_value_is_not_finite",
    }


def _insufficient_result(method: str, count: int, minimum: int) -> dict[str, Any]:
    return {
        "is_anomaly": False,
        "score": 0.0,
        "method": method,
        "reason": f"insufficient_history:n={count}, minimum={minimum}",
    }


def _modified_z_score(current: float, median: float, mad: float) -> tuple[float, str]:
    """Return a robust score, including a stable fallback for zero MAD.

    A majority-constant baseline has no measured dispersion. Treating every
    non-identical value as infinitely anomalous is too sensitive to rounding
    and small operational noise, so a 1% relative scale is used instead. A
    zero baseline still treats a non-zero observation as a strong signal.
    """
    difference = abs(current - median)
    if mad > 0:
        return 0.6745 * difference / mad, f"mad={mad:.3f}"
    if difference == 0:
        return 0.0, "mad=0.000, zero_mad_scale=exact_match"

    fallback_scale = abs(median) * 0.01
    if fallback_scale == 0:
        return float("inf"), "mad=0.000, zero_mad_scale=zero_baseline"
    return (
        0.6745 * difference / fallback_scale,
        f"mad=0.000, zero_mad_scale={fallback_scale:.6g}",
    )


def zscore_detector(
    current: float,
    history: Iterable[float],
    threshold: float = 3.0,
) -> dict[str, Any]:
    """Detect an observation using mean and population standard deviation."""
    current_value = _current_value(current)
    if current_value is None:
        return _invalid_current_result("zscore")

    values = _finite_values(history)
    if values.size < 3:
        return _insufficient_result("zscore", int(values.size), 3)

    mean = float(np.mean(values))
    std = float(np.std(values))
    if std == 0:
        score = float("inf") if current_value != mean else 0.0
    else:
        score = abs(current_value - mean) / std
    return {
        "is_anomaly": bool(score > threshold),
        "score": float(score),
        "method": "zscore",
        "reason": f"mean={mean:.3f}, std={std:.3f}, threshold={threshold}",
    }


def mad_detector(
    current: float,
    history: Iterable[float],
    threshold: float = 3.5,
) -> dict[str, Any]:
    """Detect an observation using the robust modified z-score."""
    current_value = _current_value(current)
    if current_value is None:
        return _invalid_current_result("mad")

    values = _finite_values(history)
    if values.size < 5:
        return _insufficient_result("mad", int(values.size), 5)

    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    modified_z, scale_reason = _modified_z_score(current_value, median, mad)
    return {
        "is_anomaly": bool(modified_z > threshold),
        "score": float(modified_z),
        "method": "mad",
        "reason": (
            f"median={median:.3f}, {scale_reason}, threshold={threshold}, "
            f"zero_mad_handled={str(mad == 0).lower()}"
        ),
    }


def _select_auto_history(
    history: Iterable[float],
    context: dict[str, Any] | None,
) -> tuple[np.ndarray, str]:
    """Choose a seasonal segment when it has enough usable observations."""
    all_values = _finite_values(history)
    if not context:
        return all_values, "history"

    for key in ("same_segment_history", "seasonal_history"):
        candidate = _finite_values(context.get(key))
        if candidate.size >= 3:
            return candidate, key

    return all_values, "history"


def detect_anomaly(
    current: float,
    history: Iterable[float],
    *,
    method: str = "auto",
    threshold: float = 3.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stable lab API with seasonal and robust ``auto`` detection.

    ``context['same_segment_history']`` is intended for seasonality-aware
    callers, for example the same weekday's historical row counts. If that
    segment is too short, the detector safely falls back to all history.
    A truthy ``known_event`` suppresses the actionable anomaly while retaining
    ``score`` and ``signal_detected`` so operators do not lose the raw signal.
    """
    selected_method = str(method).strip().lower()
    if selected_method == "mad":
        return mad_detector(current, history, threshold=max(threshold, 3.5))
    if selected_method == "zscore":
        return zscore_detector(current, history, threshold=threshold)
    if selected_method != "auto":
        raise ValueError(f"Unsupported method: {method}")

    current_value = _current_value(current)
    if current_value is None:
        return _invalid_current_result("auto:mad")

    values, source = _select_auto_history(history, context)
    if values.size < 3:
        result = _insufficient_result("auto:mad", int(values.size), 3)
    else:
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        score, scale_reason = _modified_z_score(current_value, median, mad)
        result = {
            "is_anomaly": bool(score > threshold),
            "score": float(score),
            "method": "auto:mad",
            "reason": (
                f"baseline={source}, median={median:.3f}, {scale_reason}, "
                f"threshold={threshold}, "
                f"zero_mad_handled={str(mad == 0).lower()}"
            ),
        }

    if context:
        if context.get("day_of_week") is not None:
            result["reason"] += f"; day_of_week={context['day_of_week']}"
        if context.get("metric_name"):
            result["reason"] += f"; metric={context['metric_name']}"
        known_event = context.get("known_event")
        if known_event not in (None, False, ""):
            signal_detected = bool(result["is_anomaly"])
            result["signal_detected"] = signal_detected
            result["suppressed"] = signal_detected
            if signal_detected:
                result["is_anomaly"] = False
            result["reason"] += (
                f"; known_event={known_event}; "
                f"suppressed_by_known_event={str(signal_detected).lower()}"
            )
    return result
