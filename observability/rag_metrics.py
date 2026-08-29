from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from observability.anomaly import zscore_detector
from observability.distribution import detect_distribution_shift


def approximate_token_lengths(texts: Iterable[str]) -> list[int]:
    # Deliberately simple proxy; no tokenizer/model download needed.
    return [len(str(t).split()) for t in texts]


def detect_text_length_shift(
    current_texts: Iterable[str],
    baseline_batch_means: Iterable[float],
    *,
    threshold: float = 3.0,
) -> dict[str, Any]:
    lengths = approximate_token_lengths(current_texts)
    current_mean = float(np.mean(lengths)) if lengths else 0.0
    result = zscore_detector(current_mean, baseline_batch_means, threshold=threshold)
    result["metric"] = "mean_text_length"
    result["current_mean"] = current_mean
    return result


def detect_embedding_norm_shift(
    current_norms: Iterable[float], baseline_norms: Iterable[float]
) -> dict[str, Any]:
    """Detect embedding-norm drift from precomputed values.

    The lab stays model-agnostic: callers can use any embedding model and pass
    its norms here. The robust distribution detector catches mean, spread, and
    shape changes, including equal-mean variance collapse/expansion.
    """
    current = list(current_norms)
    baseline = list(baseline_norms)
    result = detect_distribution_shift(current, baseline, ratio_threshold=2.0)

    current_finite = np.asarray(
        [float(value) for value in current if _is_finite_number(value)], dtype=float
    )
    baseline_finite = np.asarray(
        [float(value) for value in baseline if _is_finite_number(value)], dtype=float
    )
    result["method"] = f"embedding_norm:{result['method']}"
    result["metric"] = "embedding_norm"
    result["current_mean"] = (
        float(np.mean(current_finite)) if current_finite.size else None
    )
    result["baseline_mean"] = (
        float(np.mean(baseline_finite)) if baseline_finite.size else None
    )
    return result


def _is_finite_number(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError, OverflowError):
        return False
