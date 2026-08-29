#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "baseline"
INCOMING = ROOT / "data" / "incoming"
HISTORY = ROOT / "data" / "history" / "metrics_history.csv"


def shift_dataframe_timestamps(df: pd.DataFrame, columns: list[str], target_age_minutes: int = 5) -> pd.DataFrame:
    parsed = []
    for col in columns:
        if col in df.columns:
            parsed.append(pd.to_datetime(df[col], utc=True, errors="coerce"))
    if not parsed:
        return df
    latest = max(s.max() for s in parsed if s.notna().any())
    target = pd.Timestamp(datetime.now(timezone.utc) - timedelta(minutes=target_age_minutes))
    delta = target - latest
    for col in columns:
        if col in df.columns:
            s = pd.to_datetime(df[col], utc=True, errors="coerce")
            df[col] = (s + delta).dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    return df


def select_healthy_weekday_volume(
    df: pd.DataFrame,
    history: pd.DataFrame,
    *,
    day_of_week: int,
) -> pd.DataFrame:
    """Size the reset batch to the historical median for the current weekday.

    The generated history intentionally has lower weekend traffic. Keeping all
    600 baseline rows while only re-anchoring timestamps made ``make reset``
    report a false volume incident on weekends.
    """
    if "day_of_week" not in history or "row_count" not in history:
        return df.copy()
    same_weekday = pd.to_numeric(
        history.loc[history["day_of_week"] == day_of_week, "row_count"],
        errors="coerce",
    ).dropna()
    if same_weekday.empty:
        return df.copy()

    target_rows = max(1, int(round(float(same_weekday.median()))))
    target_rows = min(len(df), target_rows)
    return df.iloc[:target_rows].copy()


def main() -> None:
    INCOMING.mkdir(parents=True, exist_ok=True)
    orders = pd.read_csv(BASE / "orders.csv")
    history = pd.read_csv(HISTORY)
    current_weekday = datetime.now(timezone.utc).weekday()
    orders = select_healthy_weekday_volume(
        orders,
        history,
        day_of_week=current_weekday,
    )
    orders = shift_dataframe_timestamps(orders, ["created_at", "updated_at"], target_age_minutes=5)
    orders.to_csv(INCOMING / "orders.csv", index=False)

    shutil.copy2(BASE / "customers.csv", INCOMING / "customers.csv")

    docs = []
    with open(BASE / "kb_documents.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            docs.append(json.loads(line))
    # Re-anchor publish times so the starter dataset is always fresh when class runs.
    now = datetime.now(timezone.utc)
    for i, doc in enumerate(docs):
        doc["published_at"] = (now - timedelta(minutes=10 + i * 2)).isoformat()
    with open(INCOMING / "kb_documents.jsonl", "w", encoding="utf-8") as f:
        for row in docs:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Keep dbt seeds synchronized with current incoming data.
    seeds = ROOT / "dbt_project" / "seeds"
    seeds.mkdir(parents=True, exist_ok=True)
    shutil.copy2(INCOMING / "orders.csv", seeds / "orders.csv")
    shutil.copy2(INCOMING / "customers.csv", seeds / "customers.csv")

    metrics = ROOT / "reports" / "latest_metrics.json"
    if metrics.exists():
        metrics.unlink()
    print(
        "Lab reset to a healthy baseline "
        f"({len(orders)} orders for weekday={current_weekday})."
    )


if __name__ == "__main__":
    main()
