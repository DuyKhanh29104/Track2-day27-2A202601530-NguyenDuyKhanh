# Incident Report

## Incident metadata

- Incident ID: `INC-2026-08-29-ORDERS-VOLUME`
- Severity: `P2` — data-quality incident with downstream dashboard risk
- Status: `RESOLVED`; recovery verified at `2026-08-29T09:33:44Z`
- Owner: Data Reliability / Commerce Data

## Summary / What happened?

The reset orders batch contained 600 rows, all assigned to Saturday
2026-08-29. The six previous Saturdays contained
`[247, 262, 268, 235, 235, 258]` rows, with median `252.5` and MAD `12.5`.
The batch was `2.38x` the seasonal median and the same-weekday MAD detector
flagged it with score `18.75`.

The batch was structurally valid, so contract, GX, and dbt tests initially
remained green. As the static snapshot aged, freshness also crossed the
30-minute warning threshold and correctly routed the batch to quarantine.

## Detection

- Initial signal: `row_count_anomaly.is_anomaly=true` in
  `reports/latest_metrics.json`.
- Detector: `auto:mad`, `same_segment_history`, `day_of_week=5`.
- Initial observation: `2026-08-29T08:52:32Z`.
- Freshness warning observed: `2026-08-29T09:03:45Z`, age `38.0` minutes.
- Downstream symptom: the mart produced 290 completed rows and daily revenue
  `18961.04` from the anomalous batch.

## Root Cause

The confirmed root cause was in the lab reset harness, not the order schema or
dbt transformation. `scripts/reset_lab.py` re-anchored timestamps for all 600
generated baseline orders but did not adapt batch size to the current weekday.
The generated metric history intentionally models weekend traffic at roughly
43% of weekday volume, so a 600-row Saturday reset contradicted its own
seasonal baseline.

The same static batch was not refreshed during investigation, causing the
secondary freshness warning. No duplicate IDs, nulls, invalid currencies,
invalid statuses, or negative amounts were found in the original batch.

## Evidence

1. Initial volume: `600`; Saturday median: `252.5`; MAD: `12.5`; robust score:
   `18.75`; anomaly: `true`.
2. Contract/GX: no structural failures; the later freshness failure had
   severity `warning` and action `quarantine`.
3. dbt build remained successful, demonstrating that pipeline success did not
   prove business-volume correctness.
4. Code-level evidence: reset selected every baseline row regardless of
   weekday while history explicitly encoded lower weekend traffic.
5. Public fault verification after remediation:
   - duplicate key: one critical contract failure;
   - volume drop: MAD score `10.23`, anomaly `true`;
   - stale KB: freshness `190.0` minutes and KB SLO breach `true`.

## When did it start?

The anomaly was introduced when the 600-row baseline was reset onto Saturday
2026-08-29. The earliest order timestamp was `2026-08-29T05:04:46Z`; the
anomaly was first measured at `08:52:32Z`.

## Blast Radius

Dataset lineage:

```text
raw_orders
  -> stg_orders
      -> fct_daily_revenue
          -> ceo_revenue_dashboard
```

Column lineage:

```text
raw_orders.amount
  -> stg_orders.amount_usd
      -> fct_daily_revenue.daily_revenue
          -> ceo_revenue_dashboard.revenue
```

The CEO revenue dashboard was the affected consumer. KB/RAG data was not part
of this incident; its freshness and text-length signals were normal.

## Mitigation

1. Changed reset behavior to select the historical same-weekday median row
   count before re-anchoring timestamps.
2. Kept warning-level failures out of publication via a real quarantine
   artifact containing the data batch and failed-check metadata.
3. Added KB contract freshness and KB freshness SLO to the baseline report.
4. Added robust distribution and embedding-norm drift detectors so equal-mean
   shape/scale failures are no longer silently ignored.

## Recovery

At `2026-08-29T09:33:44Z`, reset selected 252 Saturday orders. The recovered
batch had row anomaly `false` with score `0.03`, order freshness `5.0` minutes,
KB freshness `10.0` minutes, and zero order/KB contract failures. The recovered
mart contained 115 completed rows and daily revenue `7675.03`.

## Verification

- [x] Orders contract healthy (`0` failed; `0` critical)
- [x] KB contract healthy (`0` failed; freshness SLO breach `false`)
- [x] GX checkpoint healthy (`0` failed expectations)
- [x] dbt build healthy (`21/21` resources)
- [x] Public suite remains scoped to the original `10` tests (`10/10` pass)
- [x] Student regression suite healthy (`21/21` tests in one separate file)
- [x] Healthy contract fixture uses relative timestamps, so freshness remains
      deterministic without weakening the production 30-minute rule
- [x] Volume returned to expected Saturday range (`252` versus median `252.5`)
- [x] Downstream mart rebuilt and business invariant test passed

## Prevention / Action Items

| Action | Owner | Status | Why |
|---|---|---|---|
| Weekday-aware healthy reset | Data Reliability | Completed | Prevent reset-generated false incidents |
| Automatic quarantine artifact | Data Platform | Completed | Preserve rejected data and actionable evidence |
| KB freshness contract and SLO | Support AI | Completed | Detect stale policy documents |
| Source manifest reconciliation | Commerce Data | Recommended | Distinguish legitimate campaigns from producer faults |
| Known-event context for campaigns | Data Reliability | Recommended | Reduce explainable anomaly noise |

## Final Handoff / Windows Reproduction

Run from the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts\reset_lab.py
.\.venv\Scripts\python.exe scripts\run_baseline.py
.\.venv\Scripts\python.exe gx\validate_orders.py
.\.venv\Scripts\dbt.exe build --project-dir dbt_project --profiles-dir dbt_project
.\.venv\Scripts\python.exe -m pytest tests_public -q
.\.venv\Scripts\python.exe -m pytest tests_extended -q
```

Expected production result: healthy order and KB contracts, row anomaly
`false`, GX pass, dbt `21/21`, public tests `10/10`, and student regression
tests `21/21`. The public suite still contains exactly 10 tests; only its
healthy timestamp fixture is relative to execution time so the test remains
valid after the lab date. The additional tests remain isolated in one file.
