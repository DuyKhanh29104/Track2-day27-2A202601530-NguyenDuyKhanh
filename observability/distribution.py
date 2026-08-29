from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def _finite_array(values: Iterable[float]) -> np.ndarray:
    cleaned: list[float] = []
    for value in values:
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if np.isfinite(numeric):
            cleaned.append(numeric)
    return np.asarray(cleaned, dtype=float)


def _robust_scale(values: np.ndarray) -> float:
    """Estimate scale without allowing one outlier to dominate it."""
    if values.size < 2:
        return 0.0
    median = float(np.median(values))
    mad_scale = 1.4826 * float(np.median(np.abs(values - median)))
    q25, q75 = np.quantile(values, [0.25, 0.75])
    iqr_scale = float(q75 - q25) / 1.349
    return max(mad_scale, iqr_scale, 0.0)


def _scale_ratio(current_scale: float, baseline_scale: float) -> float:
    if current_scale == 0 and baseline_scale == 0:
        return 1.0
    if current_scale == 0 or baseline_scale == 0:
        return float("inf")
    return max(current_scale / baseline_scale, baseline_scale / current_scale)


def _ks_statistic(current: np.ndarray, baseline: np.ndarray) -> float:
    """Compute the two-sample Kolmogorov-Smirnov distance with NumPy only."""
    current = np.sort(current)
    baseline = np.sort(baseline)
    points = np.sort(np.unique(np.concatenate([current, baseline])))
    current_cdf = np.searchsorted(current, points, side="right") / current.size
    baseline_cdf = np.searchsorted(baseline, points, side="right") / baseline.size
    return float(np.max(np.abs(current_cdf - baseline_cdf)))


def detect_distribution_shift(
    current_values: Iterable[float],
    baseline_values: Iterable[float],
    *,
    ratio_threshold: float = 3.0,
) -> dict[str, Any]:
    """Detect location, scale, or shape drift with a robust hybrid policy.

    A mean-only detector misses important failures when two populations keep
    the same mean but their spread or shape changes. This implementation uses
    a two-sample KS distance plus robust median and scale comparisons. It has
    no SciPy dependency and safely ignores malformed/non-finite observations.
    """
    if ratio_threshold <= 1:
        raise ValueError("ratio_threshold must be greater than 1")

    cur = _finite_array(current_values)
    base = _finite_array(baseline_values)
    if cur.size == 0 or base.size == 0:
        return {
            "is_anomaly": False,
            "score": 0.0,
            "method": "hybrid_ks_robust",
            "reason": "empty_or_non_finite_input",
        }

    ks_distance = _ks_statistic(cur, base)
    # Asymptotic alpha=0.05 critical value. For tiny samples this can exceed
    # one, in which case robust location/scale checks still catch clear shifts.
    ks_critical = 1.36 * np.sqrt((cur.size + base.size) / (cur.size * base.size))
    ks_normalized = ks_distance / ks_critical

    cur_median = float(np.median(cur))
    base_median = float(np.median(base))
    cur_scale = _robust_scale(cur)
    base_scale = _robust_scale(base)
    median_delta = abs(cur_median - base_median)
    if base_scale == 0:
        location_score = float("inf") if median_delta > 0 else 0.0
    else:
        location_score = median_delta / base_scale
    scale_ratio = _scale_ratio(cur_scale, base_scale)

    location_threshold = 3.5
    normalized_location = location_score / location_threshold
    normalized_scale = scale_ratio / ratio_threshold
    score = max(float(ks_normalized), float(normalized_location), float(normalized_scale))
    is_anomaly = (
        ks_distance > ks_critical
        or location_score > location_threshold
        or scale_ratio >= ratio_threshold
    )

    return {
        "is_anomaly": bool(is_anomaly),
        "score": float(score),
        "method": "hybrid_ks_robust",
        "reason": (
            f"ks={ks_distance:.4f}, ks_critical={ks_critical:.4f}; "
            f"baseline_median={base_median:.4f}, current_median={cur_median:.4f}, "
            f"location_score={location_score:.4f}; "
            f"baseline_scale={base_scale:.4f}, current_scale={cur_scale:.4f}, "
            f"scale_ratio={scale_ratio:.4f}, ratio_threshold={ratio_threshold:.4f}"
        ),
    }
