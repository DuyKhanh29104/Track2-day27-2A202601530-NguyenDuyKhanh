"""Deterministic validation for the lab's YAML data contracts.

The public API intentionally remains small: callers receive a list of issue
dictionaries.  Each issue includes a severity and an action so an ingestion
job can decide whether to block, quarantine, or merely warn.
"""
from __future__ import annotations

import math
import numbers
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
DEFAULT_ACTIONS = {
    "critical": "block",
    "warning": "quarantine",
    "info": "warn",
}
VALID_ACTIONS = {"block", "quarantine", "warn", "none"}


def _normalise_severity(value: Any) -> str:
    severity = str(value or "warning").strip().lower()
    return severity if severity in SEVERITY_ORDER else "warning"


def _action_for(severity: str, action: Any = None, *, passed: bool) -> str:
    """Resolve a contract action, keeping successful checks side-effect free."""
    if passed:
        return "none"
    requested = str(action or "").strip().lower()
    if requested in VALID_ACTIONS and requested != "none":
        return requested
    return DEFAULT_ACTIONS[_normalise_severity(severity)]


def _null_mask(series: pd.Series) -> pd.Series:
    """Treat NA and blank strings as missing values."""
    blank = series.astype("string").str.strip().eq("").fillna(False)
    return (series.isna() | blank).astype(bool)


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, numbers.Number):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _type_valid_mask(values: pd.Series, expected_type: Any) -> pd.Series:
    """Return a boolean mask without coercing strings into another type."""
    expected = str(expected_type or "").strip().lower().replace("-", "_")
    aliases = {
        "int": "integer",
        "int32": "integer",
        "int64": "integer",
        "float": "number",
        "float32": "number",
        "float64": "number",
        "numeric": "number",
        "double": "number",
        "bool": "boolean",
        "timestamp": "datetime",
        "date_time": "datetime",
    }
    expected = aliases.get(expected, expected)

    if expected == "integer":
        return values.map(
            lambda value: (
                not isinstance(value, bool)
                and isinstance(value, numbers.Integral)
            )
            or (
                not isinstance(value, bool)
                and isinstance(value, numbers.Real)
                and math.isfinite(float(value))
                and float(value).is_integer()
            )
        ).astype(bool)

    if expected in {"number", "decimal"}:
        return values.map(_is_finite_number).astype(bool)

    if expected in {"string", "str", "text"}:
        return values.map(lambda value: isinstance(value, str)).astype(bool)

    if expected == "boolean":
        return values.map(lambda value: isinstance(value, (bool,))).astype(bool)

    if expected in {"datetime", "date"}:
        non_numeric = ~values.map(lambda value: isinstance(value, numbers.Number))
        parsed = pd.to_datetime(values, errors="coerce", utc=True)
        return (non_numeric & parsed.notna()).astype(bool)

    # Unknown types should not silently pass.  A contract typo is itself a
    # validation failure, and the details field tells the operator why.
    return pd.Series(False, index=values.index, dtype=bool)


def _issue(
    check: str,
    *,
    column: str | None,
    severity: str,
    passed: bool,
    details: str,
    action: str | None = None,
) -> dict[str, Any]:
    severity = _normalise_severity(severity)
    return {
        "check": check,
        "column": column,
        "severity": severity,
        "passed": bool(passed),
        "details": details,
        "action": _action_for(severity, action, passed=passed),
    }


def load_contract(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_dataframe(df: pd.DataFrame, contract: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    # Orders uses ``columns`` while the KB contract uses ``fields``.  Supporting
    # both keeps the validator reusable without changing the stable orders API.
    columns = contract.get("columns") or contract.get("fields") or {}

    for column, rules in columns.items():
        rules = rules or {}
        severity = _normalise_severity(rules.get("severity", "warning"))
        action = rules.get("action")
        required = bool(rules.get("required", False))

        if column not in df.columns:
            if required:
                issues.append(
                    _issue(
                        "required_column",
                        column=column,
                        severity=severity,
                        passed=False,
                        details=f"Missing required column: {column}",
                        action=action,
                    )
                )
            continue

        series = df[column]
        missing = _null_mask(series)

        if required:
            null_count = int(missing.sum())
            issues.append(
                _issue(
                    "not_null",
                    column=column,
                    severity=severity,
                    passed=(null_count == 0),
                    details=f"null_count={null_count}",
                    action=action,
                )
            )

        declared_type = rules.get("type")
        if declared_type is not None:
            values = series.loc[~missing]
            valid_type = _type_valid_mask(values, declared_type)
            invalid_count = int((~valid_type).sum())
            expected = str(declared_type)
            known_type = expected.strip().lower().replace("-", "_") in {
                "int", "integer", "int32", "int64", "number", "float",
                "float32", "float64", "numeric", "double", "decimal",
                "string", "str", "text", "boolean", "bool", "datetime",
                "timestamp", "date_time", "date",
            }
            details = f"expected={expected}; invalid_count={invalid_count}"
            if not known_type:
                details += "; unsupported_contract_type=true"
            issues.append(
                _issue(
                    "type",
                    column=column,
                    severity=severity,
                    passed=known_type and invalid_count == 0,
                    details=details,
                    action=action,
                )
            )

        if rules.get("unique"):
            duplicate_count = int(series.duplicated(keep=False).sum())
            issues.append(
                _issue(
                    "unique",
                    column=column,
                    severity=severity,
                    passed=(duplicate_count == 0),
                    details=f"duplicate_rows={duplicate_count}",
                    action=action,
                )
            )

        accepted = rules.get("accepted_values")
        if accepted is not None:
            invalid_mask = ~missing & ~series.isin(accepted)
            invalid_count = int(invalid_mask.sum())
            issues.append(
                _issue(
                    "accepted_values",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}; accepted={accepted}",
                    action=action,
                )
            )

        # Numeric range support. Non-numeric non-null values fail this check as
        # well as the type check, instead of being hidden by coercion.
        if "min" in rules or "max" in rules:
            numeric = pd.to_numeric(series, errors="coerce")
            invalid = (~missing & numeric.isna()).astype(bool)
            if "min" in rules:
                invalid |= numeric < rules["min"]
            if "max" in rules:
                invalid |= numeric > rules["max"]
            invalid_count = int(invalid.fillna(False).sum())
            issues.append(
                _issue(
                    "range",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}",
                    action=action,
                )
            )

        if "min_length" in rules:
            minimum = int(rules["min_length"])
            lengths = series.map(lambda value: len(value) if isinstance(value, str) else -1)
            invalid_count = int((~missing & (lengths < minimum)).sum())
            issues.append(
                _issue(
                    "min_length",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"min_length={minimum}; invalid_count={invalid_count}",
                    action=action,
                )
            )

    freshness = contract.get("freshness") or {}
    freshness_column = freshness.get("column")
    max_delay = freshness.get("max_delay_minutes")
    if freshness_column and max_delay is not None:
        freshness_severity = _normalise_severity(freshness.get("severity", "warning"))
        freshness_action = freshness.get("action")
        if freshness_column not in df.columns:
            issues.append(
                _issue(
                    "freshness",
                    column=freshness_column,
                    severity=freshness_severity,
                    passed=False,
                    details=f"Missing freshness column: {freshness_column}",
                    action=freshness_action,
                )
            )
        else:
            timestamp_series = df[freshness_column]
            timestamp_missing = _null_mask(timestamp_series)
            parsed = pd.to_datetime(timestamp_series, errors="coerce", utc=True)
            invalid_timestamp_count = int((~timestamp_missing & parsed.isna()).sum())
            if parsed.dropna().empty:
                passed = False
                details = "no_valid_timestamps"
            elif invalid_timestamp_count:
                passed = False
                details = f"invalid_timestamp_count={invalid_timestamp_count}"
            else:
                latest = parsed.max()
                age_minutes = (
                    pd.Timestamp.now(tz="UTC") - latest
                ).total_seconds() / 60.0
                passed = age_minutes <= float(max_delay)
                details = (
                    f"latest={latest.isoformat()}; age_minutes={age_minutes:.3f}; "
                    f"max_delay_minutes={float(max_delay):g}"
                )
            issues.append(
                _issue(
                    "freshness",
                    column=freshness_column,
                    severity=freshness_severity,
                    passed=passed,
                    details=details,
                    action=freshness_action,
                )
            )

    return issues


def failed_issues(issues: list[dict[str, Any]], min_severity: str | None = None) -> list[dict[str, Any]]:
    failed = [i for i in issues if not i.get("passed", False)]
    if min_severity is None:
        return failed
    severity = _normalise_severity(min_severity)
    threshold = SEVERITY_ORDER[severity]
    return [
        i
        for i in failed
        if SEVERITY_ORDER.get(_normalise_severity(i.get("severity")), 1) >= threshold
    ]
