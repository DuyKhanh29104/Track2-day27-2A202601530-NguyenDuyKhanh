from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "latest_metrics.json"
HISTORY = ROOT / "data" / "history" / "metrics_history.csv"

st.set_page_config(page_title="Data Reliability Lab", layout="wide")
st.title("Data Reliability Game Day")
st.caption("Starter dashboard - improve it only if it helps incident decisions.")

if not REPORT.exists():
    st.warning("Run `make baseline` first to generate reports/latest_metrics.json")
    st.stop()

report = json.loads(REPORT.read_text(encoding="utf-8"))

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Orders rows", report["orders_rows"])
c2.metric("Freshness (min)", f"{report['freshness_minutes']:.1f}")
c3.metric("Contract failures", report["failed_contract_checks"])
c4.metric("Critical failures", report["critical_contract_failures"])
c5.metric("KB contract failures", report.get("kb_failed_contract_checks", 0))

needs_quarantine = bool(
    report["failed_contract_checks"]
    or report.get("kb_failed_contract_checks", 0)
    or report["row_count_anomaly"]["is_anomaly"]
)
st.metric("Incident status", "QUARANTINE / INVESTIGATE" if needs_quarantine else "HEALTHY")

st.subheader("Current signals")
st.json({
    "row_count_anomaly": report["row_count_anomaly"],
    "kb_text_length_signal": report["kb_text_length_signal"],
    "contract_slo": report["contract_slo"],
    "kb_freshness_slo": report.get("kb_freshness_slo"),
})

st.subheader("SLO and error budget")
slo = report["contract_slo"]
kb_slo = report.get("kb_freshness_slo", {})
st.json({
    "critical_contract": {
        "target": slo.get("target"),
        "burn_rate": slo.get("burn_rate"),
        "remaining_error_budget_fraction": slo.get("remaining_error_budget_fraction"),
        "breached": slo.get("breached"),
    },
    "rag_index_freshness": {
        "target": kb_slo.get("target"),
        "burn_rate": kb_slo.get("burn_rate"),
        "remaining_error_budget_fraction": kb_slo.get("remaining_error_budget_fraction"),
        "breached": kb_slo.get("breached"),
    },
})
st.caption("Multi-window paging requires aggregated short/long-window burn metrics from repeated runs.")

history = pd.read_csv(HISTORY)
st.subheader("Historical row count")
st.line_chart(history.set_index("date")[["row_count"]])

st.subheader("Example blast radius")
st.write("stg_orders -> " + " -> ".join(report["sample_blast_radius_from_stg_orders"]))

st.info("Owner: commerce-data / support-ai. Follow the incident report recovery checklist before publication.")
