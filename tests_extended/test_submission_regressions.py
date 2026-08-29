"""Student-added regression tests; the original tests_public suite is untouched."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd

from gx.validate_orders import write_quarantine_artifact
from observability.lineage import extract_dbt_asset_graph, extract_dbt_dataset_graph
from scripts.reset_lab import select_healthy_weekday_volume
from student_api import (
    column_downstream,
    detect_distribution,
    detect_metric,
    downstream_assets,
    multiwindow_burn,
    rag_embedding_shift,
    slo_status,
    validate_orders,
)


ROOT = Path(__file__).resolve().parents[1]
KB_CONTRACT = ROOT / "contracts" / "kb_contract.yaml"


def _failed(issues):
    return [issue for issue in issues if not issue["passed"]]


def test_auto_detects_volume_drop_with_robust_baseline():
    history = [1000, 1010, 995, 1008, 1004, 1012, 998]
    result = detect_metric(300, history, method="auto")
    assert result["is_anomaly"] is True
    assert result["method"] == "auto:mad"


def test_auto_uses_same_segment_for_legitimate_weekend_pattern():
    mixed_history = [
        1000, 1010, 995, 1008, 1004, 1012, 998,
        420, 430, 415, 425, 418, 432,
    ]
    result = detect_metric(
        425,
        mixed_history,
        method="auto",
        context={
            "metric_name": "row_count",
            "day_of_week": 5,
            "same_segment_history": [420, 430, 415, 425, 418, 432],
        },
    )
    assert result["is_anomaly"] is False
    assert "baseline=same_segment_history" in result["reason"]


def test_auto_is_robust_to_historical_outlier():
    result = detect_metric(
        101,
        [100, 101, 99, 100, 100, 102, 1000],
        method="auto",
    )
    assert result["is_anomaly"] is False


def test_auto_handles_zero_mad_as_signal():
    result = detect_metric(50, [100, 100, 100, 100, 100], method="auto")
    assert result["is_anomaly"] is True
    assert "zero_mad_handled=true" in result["reason"]


def test_auto_zero_mad_tolerates_small_noise_despite_sparse_outlier():
    result = detect_metric(101, [100, 100, 100, 100, 100, 1000], method="auto")
    assert result["is_anomaly"] is False
    assert result["score"] < 3.0


def test_auto_known_event_suppresses_an_actionable_signal():
    result = detect_metric(
        300,
        [1000, 1010, 995, 1008, 1004, 1012, 998],
        method="auto",
        context={"known_event": "planned_migration"},
    )
    assert result["signal_detected"] is True
    assert result["suppressed"] is True
    assert result["is_anomaly"] is False


def test_stale_kb_is_quarantined_by_freshness_contract():
    stale = datetime.now(timezone.utc) - timedelta(hours=3)
    df = pd.DataFrame([
        {
            "doc_id": "refund-policy",
            "version": 4,
            "effective_at": stale.isoformat(),
            "published_at": stale.isoformat(),
            "source_uri": "policy/refund-v4.pdf",
            "content": "Customers may request a refund under the documented conditions.",
        }
    ])
    freshness = next(
        issue
        for issue in _failed(validate_orders(df, KB_CONTRACT))
        if issue["check"] == "freshness"
    )
    assert freshness["severity"] == "warning"
    assert freshness["action"] == "quarantine"


def test_equal_mean_scale_shift_is_detected():
    baseline = [0, 0, 0, 0, 0, 0]
    current = [-10, 10, -10, 10, -10, 10]
    result = detect_distribution(current, baseline)
    assert result["is_anomaly"] is True
    assert result["method"] == "hybrid_ks_robust"


def test_stable_distribution_is_not_anomaly():
    baseline = [0.9, 1.0, 1.1, 1.0, 0.95, 1.05]
    current = [0.92, 1.02, 1.08, 0.98, 0.96, 1.04]
    assert detect_distribution(current, baseline)["is_anomaly"] is False


def test_quarantine_action_persists_data_and_evidence(tmp_path):
    df = pd.DataFrame([{"order_id": 1, "status": "pending"}])
    issues = [
        {
            "check": "freshness",
            "column": "updated_at",
            "severity": "warning",
            "passed": False,
            "details": "age_minutes=45",
            "action": "quarantine",
        }
    ]
    artifact = write_quarantine_artifact(
        df,
        issues,
        tmp_path,
        observed_at=datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc),
    )
    written = pd.read_csv(artifact["data"])
    metadata = json.loads(Path(artifact["metadata"]).read_text(encoding="utf-8"))
    assert written.to_dict("records") == df.to_dict("records")
    assert metadata["action"] == "quarantine"
    assert metadata["failed_checks"][0]["check"] == "freshness"


def test_transitive_downstream_columns():
    graph = {
        "raw_orders.amount": ["stg_orders.amount_usd"],
        "stg_orders.amount_usd": ["fct_daily_revenue.daily_revenue"],
        "fct_daily_revenue.daily_revenue": ["ceo_revenue_dashboard.revenue"],
    }
    assert column_downstream(graph, "raw_orders.amount") == [
        "stg_orders.amount_usd",
        "fct_daily_revenue.daily_revenue",
        "ceo_revenue_dashboard.revenue",
    ]


def test_lineage_traversal_is_cycle_safe():
    graph = {"a": ["b"], "b": ["c"], "c": ["a"]}
    assert downstream_assets(graph, "a") == ["b", "c"]


def test_manifest_graph_supports_depends_on_and_filters_test_nodes(tmp_path):
    manifest = {
        "nodes": {
            "seed.lab.orders": {"resource_type": "seed", "name": "orders"},
            "model.lab.stg_orders": {
                "resource_type": "model",
                "name": "stg_orders",
                "depends_on": {"nodes": ["seed.lab.orders"]},
            },
            "model.lab.revenue": {
                "resource_type": "model",
                "name": "revenue",
                "depends_on": {"nodes": ["model.lab.stg_orders"]},
            },
            "test.lab.unique_orders": {
                "resource_type": "test",
                "name": "unique_orders",
                "depends_on": {"nodes": ["model.lab.stg_orders"]},
            },
        },
        "child_map": {
            "seed.lab.orders": ["model.lab.stg_orders"],
            "model.lab.stg_orders": ["model.lab.revenue", "test.lab.unique_orders"],
            "model.lab.revenue": [],
            "test.lab.unique_orders": [],
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    graph = extract_dbt_dataset_graph(path)
    assert graph["model.lab.stg_orders"] == [
        "model.lab.revenue",
        "test.lab.unique_orders",
    ]
    assert extract_dbt_asset_graph(path) == {
        "orders": ["stg_orders"],
        "stg_orders": ["revenue"],
        "revenue": [],
    }


def test_manifest_graph_falls_back_to_parent_map(tmp_path):
    manifest = {
        "nodes": {
            "model.lab.a": {"resource_type": "model", "name": "a"},
            "model.lab.b": {"resource_type": "model", "name": "b"},
        },
        "parent_map": {
            "model.lab.a": [],
            "model.lab.b": ["model.lab.a"],
        },
    }
    path = tmp_path / "manifest_parent_map.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert extract_dbt_dataset_graph(path) == {
        "model.lab.a": ["model.lab.b"],
        "model.lab.b": [],
    }


def test_embedding_norm_shift_is_detected():
    baseline = [0.98, 1.00, 1.02, 0.99, 1.01, 1.00]
    current = [1.95, 2.00, 2.05, 1.98, 2.02, 2.00]
    result = rag_embedding_shift(current, baseline)
    assert result["is_anomaly"] is True
    assert result["metric"] == "embedding_norm"
    assert result["method"].startswith("embedding_norm:")


def test_stable_embedding_norms_are_not_anomaly():
    baseline = [0.98, 1.00, 1.02, 0.99, 1.01, 1.00]
    current = [0.99, 1.01, 1.01, 0.98, 1.02, 1.00]
    assert rag_embedding_shift(current, baseline)["is_anomaly"] is False


def test_reset_volume_uses_same_weekday_median():
    orders = pd.DataFrame({"order_id": range(600)})
    history = pd.DataFrame(
        {
            "day_of_week": [5, 5, 5, 5, 1, 1],
            "row_count": [235, 247, 258, 268, 590, 610],
        }
    )
    selected = select_healthy_weekday_volume(orders, history, day_of_week=5)
    assert len(selected) == 252
    assert selected["order_id"].is_unique


def test_transient_short_window_spike_does_not_page():
    result = multiwindow_burn(short_window_burn=20.0, long_window_burn=1.0)
    assert result["page"] is False
    assert result["severity"] == "warning"
    assert result["reason"] == "transient_short_window_spike"


def test_sustained_fast_burn_pages():
    result = multiwindow_burn(short_window_burn=20.0, long_window_burn=10.0)
    assert result["page"] is True
    assert result["severity"] == "critical"
    assert result["reason"] == "sustained_fast_burn"


def test_sustained_six_x_burn_pages_without_requiring_fourteen_x():
    result = multiwindow_burn(short_window_burn=8.0, long_window_burn=7.0)
    assert result["page"] is True
    assert result["severity"] == "critical"


def test_slo_exact_error_budget_boundary_is_not_a_breach():
    result = slo_status(0.9, bad_events=1, total_events=10)
    assert result["burn_rate"] == 1.0
    assert result["remaining_error_budget_fraction"] == 0.0
    assert result["breached"] is False
