#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observability.anomaly import detect_anomaly
from observability.lineage import get_column_downstream, get_downstream_assets
from observability.rag_metrics import detect_text_length_shift
from observability.slo import calculate_slo
from src.contract_validator import failed_issues, load_contract, validate_dataframe
from src.io_utils import load_jsonl


def main() -> None:
    orders = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    history = pd.read_csv(ROOT / "data" / "history" / "metrics_history.csv")
    contract = load_contract(ROOT / "contracts" / "orders_contract.yaml")
    issues = validate_dataframe(orders, contract)
    failed = failed_issues(issues)
    critical_failed = failed_issues(issues, min_severity="critical")

    # Public example: segment by weekday before applying the simple detector.
    # Hidden evaluation still challenges students to make detect_metric(..., context=...)
    # context-aware instead of relying on caller-side preprocessing.
    current_dow = datetime.now(timezone.utc).weekday()
    segment = history.loc[history["day_of_week"] == current_dow, "row_count"].tail(8).tolist()
    row_history = segment if len(segment) >= 3 else history["row_count"].tail(14).tolist()
    row_result = detect_anomaly(
        len(orders),
        row_history,
        method="auto",
        context={
            "metric_name": "row_count",
            "day_of_week": current_dow,
            "same_segment_history": segment,
        },
    )

    updated = pd.to_datetime(orders["updated_at"], utc=True, errors="coerce")
    freshness_minutes = (
        pd.Timestamp(datetime.now(timezone.utc)) - updated.max()
    ).total_seconds() / 60.0

    docs = load_jsonl(ROOT / "data" / "incoming" / "kb_documents.jsonl")
    kb_df = pd.DataFrame(docs)
    kb_contract = load_contract(ROOT / "contracts" / "kb_contract.yaml")
    kb_issues = validate_dataframe(kb_df, kb_contract)
    kb_failed = failed_issues(kb_issues)
    kb_critical_failed = failed_issues(kb_issues, min_severity="critical")
    kb_freshness_failed = any(
        issue["check"] == "freshness" and not issue["passed"]
        for issue in kb_issues
    )
    published = pd.to_datetime(kb_df.get("published_at"), utc=True, errors="coerce")
    kb_freshness_minutes = (
        (pd.Timestamp.now(tz="UTC") - published.max()).total_seconds() / 60.0
        if published is not None and published.notna().any()
        else None
    )
    text_result = detect_text_length_shift(
        [d["content"] for d in docs], history["mean_text_length"].tail(14).tolist()
    )

    # Demo SLO: one check event for this run.
    bad = 1 if critical_failed else 0
    contract_slo = calculate_slo(0.999, bad_events=bad, total_events=1)
    with open(ROOT / "lab_config.yaml", "r", encoding="utf-8") as f:
        lab_config = yaml.safe_load(f) or {}
    kb_slo_target = float(
        lab_config.get("slo", {}).get("rag_index_freshness", {}).get("target", 0.99)
    )
    kb_freshness_slo = calculate_slo(
        kb_slo_target,
        bad_events=int(kb_freshness_failed),
        total_events=1,
    )

    with open(ROOT / "data" / "baseline" / "lineage_graph.json", "r", encoding="utf-8") as f:
        lineage_payload = json.load(f)
    lineage = lineage_payload["dataset_lineage"]
    blast_radius = get_downstream_assets(lineage, "stg_orders")
    column_blast_radius = get_column_downstream(
        lineage_payload.get("column_lineage", {}), "raw_orders.amount"
    )

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "orders_rows": int(len(orders)),
        "failed_contract_checks": len(failed),
        "critical_contract_failures": len(critical_failed),
        "row_count_anomaly": row_result,
        "freshness_minutes": freshness_minutes,
        "kb_failed_contract_checks": len(kb_failed),
        "kb_critical_contract_failures": len(kb_critical_failed),
        "kb_freshness_minutes": kb_freshness_minutes,
        "kb_freshness_slo": kb_freshness_slo,
        "kb_text_length_signal": text_result,
        "contract_slo": contract_slo,
        "sample_blast_radius_from_stg_orders": blast_radius,
        "sample_column_blast_radius_from_raw_orders_amount": column_blast_radius,
    }
    out = ROOT / "reports" / "latest_metrics.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("=== DATA RELIABILITY BASELINE ===")
    print(f"orders rows              : {len(orders)}")
    print(f"contract failed checks   : {len(failed)}")
    print(f"critical contract fails  : {len(critical_failed)}")
    print(f"row-count anomaly        : {row_result['is_anomaly']} ({row_result['method']}, score={row_result['score']:.2f})")
    print(f"freshness minutes        : {freshness_minutes:.1f}")
    print(f"KB contract failures     : {len(kb_failed)}")
    print(f"KB critical failures     : {len(kb_critical_failed)}")
    print(
        "KB freshness minutes    : "
        f"{kb_freshness_minutes:.1f}" if kb_freshness_minutes is not None
        else "KB freshness minutes    : unavailable"
    )
    print(f"KB freshness SLO breach  : {kb_freshness_slo['breached']}")
    print(f"KB length anomaly        : {text_result['is_anomaly']}")
    print(f"sample blast radius      : {', '.join(blast_radius)}")
    print(f"sample column blast      : {', '.join(column_blast_radius)}")
    print(f"report                    : {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
