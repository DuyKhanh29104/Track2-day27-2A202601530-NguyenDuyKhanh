#!/usr/bin/env python3
"""Great Expectations validation flow for the orders contract.

The script deliberately keeps the custom contract validator as the source of
truth for semantic type and freshness checks. Great Expectations provides the
reusable Suite/ValidationDefinition/Checkpoint flow for structural and value
expectations, while the custom action turns GX failure severity into an
operational route.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import great_expectations as gx
    from great_expectations.checkpoint.actions import ValidationAction
except ImportError as exc:  # friendlier classroom failure
    raise SystemExit("great_expectations is not installed. Run: pip install -r requirements.txt") from exc

from src.contract_validator import DEFAULT_ACTIONS, SEVERITY_ORDER, load_contract, validate_dataframe


SUITE_NAME = "orders_contract_suite"
VALIDATION_NAME = "orders_contract_validation"
CHECKPOINT_NAME = "orders_contract_checkpoint"
DATA_SOURCE_NAME = "orders_pandas"
ASSET_NAME = "orders_dataframe"
BATCH_NAME = "whole_orders"


def _severity_value(value: Any) -> str:
    value = getattr(value, "value", value)
    severity = str(value or "warning").strip().lower()
    return severity if severity in SEVERITY_ORDER else "warning"


def _route_for(severity: str | None) -> str:
    if severity is None:
        return "none"
    return DEFAULT_ACTIONS.get(_severity_value(severity), "warn")


def _highest_severity(severities: list[str]) -> str | None:
    if not severities:
        return None
    return max(severities, key=lambda value: SEVERITY_ORDER.get(value, 1))


def summarize_checkpoint(checkpoint_result: Any) -> dict[str, Any]:
    """Summarize failed GX expectations into an operational action."""
    failed_by_severity: Counter[str] = Counter()
    failed_expectations: list[str] = []

    for validation_result in checkpoint_result.run_results.values():
        for result in validation_result.results:
            if result.success:
                continue
            severity = _severity_value(result.expectation_config.severity)
            failed_by_severity[severity] += 1
            failed_expectations.append(result.expectation_config.type)

    highest = _highest_severity(list(failed_by_severity.elements()))
    return {
        "failed_expectations": len(failed_expectations),
        "failed_by_severity": dict(failed_by_severity),
        "highest_severity": highest or "none",
        "action": _route_for(highest),
    }


class SeverityRoutingAction(ValidationAction):
    """Local GX action that maps expectation severity to a runbook route.

    It has no external side effects, which makes it safe for the local lab.
    The returned dictionary is also summarized by the CLI after the checkpoint
    run so the route is visible in the evidence collected by students.
    """

    type: str = "severity_routing"
    name: str = "severity_routing"

    def run(self, checkpoint_result: Any, action_context: Any = None) -> dict[str, Any]:
        return summarize_checkpoint(checkpoint_result)


def build_expectation_suite(contract: dict[str, Any]) -> Any:
    """Build a GX Suite from the orders contract's deterministic rules."""
    suite = gx.ExpectationSuite(
        name=SUITE_NAME,
        meta={
            "dataset": contract.get("dataset", "orders"),
            "owner": contract.get("owner", "unknown"),
            "source": "contracts/orders_contract.yaml",
        },
    )

    for column, rules in (contract.get("columns") or {}).items():
        rules = rules or {}
        severity = _severity_value(rules.get("severity", "warning"))
        action = rules.get("action") or DEFAULT_ACTIONS[severity]
        meta = {
            "contract_column": column,
            "contract_severity": severity,
            "contract_action": action,
        }

        if rules.get("required"):
            suite.add_expectation(
                gx.expectations.ExpectColumnToExist(
                    column=column,
                    severity=severity,
                    meta=meta,
                )
            )
            suite.add_expectation(
                gx.expectations.ExpectColumnValuesToNotBeNull(
                    column=column,
                    severity=severity,
                    meta=meta,
                )
            )

        if rules.get("unique"):
            suite.add_expectation(
                gx.expectations.ExpectColumnValuesToBeUnique(
                    column=column,
                    severity=severity,
                    meta=meta,
                )
            )

        accepted = rules.get("accepted_values")
        if accepted is not None:
            suite.add_expectation(
                gx.expectations.ExpectColumnValuesToBeInSet(
                    column=column,
                    value_set=list(accepted),
                    severity=severity,
                    meta=meta,
                )
            )

        if "min" in rules or "max" in rules:
            suite.add_expectation(
                gx.expectations.ExpectColumnValuesToBeBetween(
                    column=column,
                    min_value=rules.get("min"),
                    max_value=rules.get("max"),
                    severity=severity,
                    meta=meta,
                )
            )

    return suite


def run_checkpoint(df: pd.DataFrame, contract: dict[str, Any]) -> Any:
    """Create and run a fresh ephemeral GX Checkpoint for one dataframe."""
    context = gx.get_context()
    data_source = context.data_sources.add_pandas(DATA_SOURCE_NAME)
    asset = data_source.add_dataframe_asset(name=ASSET_NAME)
    batch_definition = asset.add_batch_definition_whole_dataframe(BATCH_NAME)

    suite = build_expectation_suite(contract)
    context.suites.add_or_update(suite)

    validation_definition = gx.ValidationDefinition(
        name=VALIDATION_NAME,
        data=batch_definition,
        suite=suite,
    )
    context.validation_definitions.add_or_update(validation_definition)

    checkpoint = gx.Checkpoint(
        name=CHECKPOINT_NAME,
        validation_definitions=[validation_definition],
        actions=[SeverityRoutingAction()],
        result_format="SUMMARY",
    )
    context.checkpoints.add_or_update(checkpoint)
    return checkpoint.run(batch_parameters={"dataframe": df})


def summarize_contract(issues: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [issue for issue in issues if not issue.get("passed", False)]
    by_severity = Counter(_severity_value(issue.get("severity")) for issue in failed)
    highest = _highest_severity(list(by_severity.elements()))
    return {
        "failed_checks": len(failed),
        "failed_by_severity": dict(by_severity),
        "highest_severity": highest or "none",
        "action": _route_for(highest),
    }


def write_quarantine_artifact(
    df: pd.DataFrame,
    issues: list[dict[str, Any]],
    output_dir: str | Path,
    *,
    observed_at: datetime | None = None,
) -> dict[str, str]:
    """Persist a recoverable copy of a batch routed to quarantine."""
    timestamp = observed_at or datetime.now(timezone.utc)
    stamp = timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    data_path = destination / f"orders_{stamp}.csv"
    metadata_path = destination / f"orders_{stamp}.json"

    failed = [issue for issue in issues if not issue.get("passed", False)]
    df.to_csv(data_path, index=False)
    metadata_path.write_text(
        json.dumps(
            {
                "dataset": "orders",
                "observed_at": timestamp.astimezone(timezone.utc).isoformat(),
                "row_count": int(len(df)),
                "action": "quarantine",
                "failed_checks": failed,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return {"data": str(data_path), "metadata": str(metadata_path)}


def main() -> None:
    df = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    contract = load_contract(ROOT / "contracts" / "orders_contract.yaml")

    contract_issues = validate_dataframe(df, contract)
    contract_summary = summarize_contract(contract_issues)
    checkpoint_result = run_checkpoint(df, contract)
    gx_summary = summarize_checkpoint(checkpoint_result)
    quarantine_artifact = None
    if "quarantine" in {contract_summary["action"], gx_summary["action"]}:
        quarantine_artifact = write_quarantine_artifact(
            df,
            contract_issues,
            ROOT / "reports" / "quarantine",
        )

    print("=== GREAT EXPECTATIONS ORDERS CHECKPOINT ===")
    for validation_result in checkpoint_result.run_results.values():
        for result in validation_result.results:
            expectation = result.expectation_config.type
            severity = _severity_value(result.expectation_config.severity)
            print(f"{expectation:<48} severity={severity:<8} success={result.success}")

    print("\n=== CONTRACT VALIDATION ROUTING ===")
    print(f"failed checks          : {contract_summary['failed_checks']}")
    print(f"failed by severity     : {contract_summary['failed_by_severity']}")
    print(f"contract action        : {contract_summary['action']}")
    for issue in contract_issues:
        if not issue["passed"]:
            print(
                f"{issue['check']}[{issue['column']}] "
                f"severity={issue['severity']} action={issue['action']} "
                f"details={issue['details']}"
            )

    print("\n=== CHECKPOINT RESULT ===")
    print(f"GX result              : {'PASS' if checkpoint_result.success else 'FAIL'}")
    print(f"GX failed expectations : {gx_summary['failed_expectations']}")
    print(f"GX action              : {gx_summary['action']}")
    if quarantine_artifact:
        print(f"quarantine data         : {quarantine_artifact['data']}")
        print(f"quarantine metadata     : {quarantine_artifact['metadata']}")


if __name__ == "__main__":
    main()
