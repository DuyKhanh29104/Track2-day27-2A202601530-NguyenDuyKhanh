from __future__ import annotations

import math
import numbers
from decimal import Decimal
from typing import Any


def calculate_slo(target: float, bad_events: int, total_events: int) -> dict[str, Any]:
    if isinstance(target, bool) or not isinstance(target, numbers.Real):
        raise ValueError("target must be a finite number")
    target_value = float(target)
    if not math.isfinite(target_value) or not 0 < target_value < 1:
        raise ValueError("target must be between 0 and 1 (exclusive)")
    if (
        isinstance(bad_events, bool)
        or isinstance(total_events, bool)
        or not isinstance(bad_events, numbers.Integral)
        or not isinstance(total_events, numbers.Integral)
    ):
        raise ValueError("event counts must be integers")
    if bad_events < 0 or total_events < 0 or bad_events > total_events:
        raise ValueError("invalid event counts")
    # Decimal arithmetic prevents an exactly exhausted budget from becoming a
    # false breach due to binary floating-point representation.
    allowed_decimal = Decimal("1") - Decimal(str(target_value))
    allowed_bad_rate = float(allowed_decimal)
    if total_events == 0:
        return {
            "target": target_value,
            "actual_bad_rate": 0.0,
            "allowed_bad_rate": allowed_bad_rate,
            "burn_rate": 0.0,
            "remaining_error_budget_fraction": 1.0,
            "breached": False,
        }
    actual_decimal = Decimal(int(bad_events)) / Decimal(int(total_events))
    burn_decimal = actual_decimal / allowed_decimal
    actual_bad_rate = float(actual_decimal)
    burn_rate = float(burn_decimal)
    consumed_fraction = min(1.0, burn_rate)
    return {
        "target": target_value,
        "actual_bad_rate": actual_bad_rate,
        "allowed_bad_rate": allowed_bad_rate,
        "burn_rate": burn_rate,
        "remaining_error_budget_fraction": max(0.0, 1.0 - consumed_fraction),
        "breached": bool(actual_decimal > allowed_decimal),
    }


SHORT_WINDOW_PAGE_BURN_THRESHOLD = 6.0
LONG_WINDOW_PAGE_BURN_THRESHOLD = 6.0
FAST_BURN_THRESHOLD = 14.4


def evaluate_multiwindow_burn(
    *,
    short_window_burn: float,
    long_window_burn: float,
    policy: str = "starter",
) -> dict[str, Any]:
    """Evaluate a two-window burn-rate alert policy.

    The short window catches a sudden increase in failures while the long
    window confirms that the increase is sustained. A page requires both
    windows to burn at least 6x; 14.4x remains exposed as the conventional
    fast-burn marker. Requiring both windows prevents a short spike from
    paging while still catching sustained 6x-14.4x consumption.

    ``policy`` is retained for compatibility with the starter signature.  It
    is returned as metadata so callers can record which policy was evaluated.
    """
    if not isinstance(policy, str) or not policy.strip():
        raise ValueError("policy must be a non-empty string")
    if (
        isinstance(short_window_burn, bool)
        or not isinstance(short_window_burn, numbers.Real)
        or not math.isfinite(short_window_burn)
        or short_window_burn < 0
    ):
        raise ValueError("short_window_burn must be a finite non-negative number")
    if (
        isinstance(long_window_burn, bool)
        or not isinstance(long_window_burn, numbers.Real)
        or not math.isfinite(long_window_burn)
        or long_window_burn < 0
    ):
        raise ValueError("long_window_burn must be a finite non-negative number")

    sustained_fast_burn = (
        short_window_burn >= SHORT_WINDOW_PAGE_BURN_THRESHOLD
        and long_window_burn >= LONG_WINDOW_PAGE_BURN_THRESHOLD
    )
    short_spike = (
        short_window_burn >= SHORT_WINDOW_PAGE_BURN_THRESHOLD
        and long_window_burn < LONG_WINDOW_PAGE_BURN_THRESHOLD
    )
    long_window_elevated = (
        long_window_burn >= LONG_WINDOW_PAGE_BURN_THRESHOLD
        and short_window_burn < SHORT_WINDOW_PAGE_BURN_THRESHOLD
    )

    if sustained_fast_burn:
        page = True
        severity = "critical"
        reason = "sustained_fast_burn"
    elif short_spike:
        page = False
        severity = "warning"
        reason = "transient_short_window_spike"
    elif long_window_elevated:
        page = False
        severity = "warning"
        reason = "long_window_burn_elevated_but_not_fast"
    else:
        page = False
        severity = "info"
        reason = "burn_within_multiwindow_policy"

    return {
        "page": page,
        "severity": severity,
        "reason": reason,
        "short_window_burn": short_window_burn,
        "long_window_burn": long_window_burn,
        "short_window_threshold": SHORT_WINDOW_PAGE_BURN_THRESHOLD,
        "long_window_threshold": LONG_WINDOW_PAGE_BURN_THRESHOLD,
        "fast_burn_threshold": FAST_BURN_THRESHOLD,
        "policy": policy,
    }
