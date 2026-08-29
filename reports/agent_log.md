# AI Agent Decision Log

Khong can copy full conversation. Ghi cac decision quan trong.

## Decision 1
- Hypothesis: Một customer có nhiều dòng `is_active = true` có thể làm phép
  join trong `fct_daily_revenue` nhân đôi số order và doanh thu.
- Prompt / request to agent: Hoàn thiện Phase 2 và bảo vệ transformation logic
  bằng dbt tests/unit tests.
- Agent proposal: Dedupe tập active customer theo `customer_id` trước khi join;
  thêm unit test với hai active versions và expected revenue là 170.0.
- Evidence/test: Unit test
  `duplicate_active_customer_does_not_inflate_revenue` pass trong `dbt build`.
- Accept / reject / revise: Accept.
- Why: Join vẫn giữ được bước kiểm tra active customer nhưng không còn
  cardinality gây revenue inflation.

## Decision 2
- Hypothesis: Generic data tests không đủ để chứng minh phép biến đổi tạo ra
  đúng business result.
- Prompt / request to agent: Phân biệt data-quality tests với transformation
  logic tests trong Phase 2.
- Agent proposal: Thêm `relationships`/`unique` generic tests, singular test
  đối chiếu revenue với completed orders, và giữ unit test với input/expected
  output cố định.
- Evidence/test: `dbt build` pass 21/21 resource; `dbt test` pass 16/16;
  public interface checks passed with a time-aligned freshness fixture.
- Accept / reject / revise: Accept.
- Why: Ba lớp kiểm tra bắt được lần lượt lỗi dữ liệu, business invariant và
  logic transformation.

## Decision 3
- Hypothesis: So sánh một batch cuối tuần với baseline ngày thường có thể tạo
  false positive dù traffic cuối tuần thấp là bình thường.
- Prompt / request to agent: Hoàn thiện Phase 3 anomaly detection với
  seasonality và robust statistics.
- Agent proposal: `auto` ưu tiên `same_segment_history` (ví dụ cùng thứ trong
  tuần), sau đó dùng median/MAD thay cho mean/std.
- Evidence/test: Pattern cuối tuần `[420, 430, 415, 425, 418, 432]` với
  current `425` trả về `is_anomaly=False`; reason ghi rõ baseline segment.
- Accept / reject / revise: Accept.
- Why: Detector không trộn các traffic regime khác nhau và vẫn giữ được
  signal volume drop thực sự.

## Decision 4
- Hypothesis: Outlier lịch sử và MAD bằng 0 có thể làm detector mean/std hoặc
  MAD starter thiếu ổn định.
- Prompt / request to agent: Bảo vệ anomaly detector trước outlier, dữ liệu
  không hữu hạn và baseline hằng.
- Agent proposal: Lọc observation không hữu hạn, dùng median/MAD robust và coi
  giá trị khác baseline hằng là anomaly với score vô hạn.
- Evidence/test: Outlier `1000` không làm current `101` thành anomaly; baseline
  hằng `100` phát hiện current `50`; volume-drop fault 150/600 trả về
  `auto:mad`, score `5.53`, anomaly `True`.
- Accept / reject / revise: Accept.
- Why: Behavior deterministic, giải thích được và không cần ML phức tạp.

## Decision 5
- Hypothesis: Lineage direct-only không đủ để xác định toàn bộ blast radius của
  một dataset hoặc column bị lỗi.
- Prompt / request to agent: Hoàn thiện Phase 4 với transitive dataset/column
  lineage.
- Agent proposal: Dùng BFS cycle-safe, giữ thứ tự ổn định và loại node bắt đầu
  khỏi kết quả.
- Evidence/test: `stg_orders` trả về
  `fct_daily_revenue -> ceo_revenue_dashboard`; `raw_orders.amount` trả về
  ba downstream columns; cycle test pass.
- Accept / reject / revise: Accept.
- Why: Bao phủ đúng toàn bộ downstream chain mà không lặp vô hạn khi graph có
  cycle.

## Decision 6
- Hypothesis: dbt manifest chứa cả test/unit-test nodes nên nếu dùng trực tiếp
  sẽ làm blast-radius report bị nhiễu.
- Prompt / request to agent: Parse manifest sau `dbt build` và tạo asset graph
  phục vụ điều tra incident.
- Agent proposal: Giữ parser unique-id tương thích, bổ sung fallback từ
  `depends_on`/`parent_map`, và thêm asset graph lọc test nodes, map về tên
  model/seed dễ đọc.
- Evidence/test: Manifest thật parse thành 21 nodes; filtered graph xác định
  `stg_orders -> fct_daily_revenue`; dbt build pass 21/21.
- Accept / reject / revise: Accept.
- Why: Vừa giữ metadata gốc cho trace kỹ thuật, vừa có graph gọn cho RCA.

## Decision 7
- Hypothesis: Chỉ nhìn error budget đã tiêu thụ chưa đủ để phân biệt một spike
  ngắn với một incident kéo dài.
- Prompt / request to agent: Hoàn thiện Phase 5 với burn-rate policy hai cửa sổ,
  trong đó short window bắt spike và long window xác nhận sustained burn.
- Agent proposal: Dùng ngưỡng sustained `short >= 6x` và `long >= 6x`, đồng
  thời giữ `14.4x` làm fast-burn marker; chỉ page khi cả hai cửa sổ xác nhận,
  còn spike ngắn chỉ tạo warning.
- Evidence/test: Case `20x/1x` trả `page=False`, `warning`, reason
  `transient_short_window_spike`; case `20x/10x` trả `page=True`, `critical`.
- Accept / reject / revise: Accept.
- Why: Policy tránh paging theo một cửa sổ ngắn đơn lẻ nhưng vẫn bắt được
  fast burn kéo dài.

## Decision 8
- Hypothesis: SLO output cần thể hiện đầy đủ phép tính để operator kiểm tra
  error budget và để hidden evaluation gọi qua stable API.
- Prompt / request to agent: Kiểm chứng case bắt buộc SLO 99.5%, 2 bad checks /
  100 checks và các input biên.
- Agent proposal: Giữ `allowed_bad_rate`, `actual_bad_rate`, `burn_rate`,
  `remaining_error_budget_fraction`, `breached`; zero events an toàn và từ chối
  target/count không hợp lệ.
- Evidence/test: `0.995, 2, 100` cho allowed `0.005`, actual `0.02`, burn `4.0`,
  remaining budget `0.0`, breach `True`; zero events không breach.
- Accept / reject / revise: Accept.
- Why: Kết quả có thể audit trực tiếp và không tạo false breach khi chưa có
  event nào.

## Phase 6 Investigation Note
- Scope: Điều tra incoming order batch bằng contract/GX, dbt, anomaly, SLO,
  lineage và raw-data exploration có lý do; không xem fault generator.
- Evidence: `600` rows cùng ngày 2026-08-29; Saturday history có median
  `252.5`, MAD `12.5`; detector `auto:mad` cho score `18.75`; contract/GX
  ban đầu `0` failures; dbt `21/21` pass; freshness ban đầu `26.8` phút.
- RCA revised: `reset_lab.py` giữ toàn bộ 600 rows khi re-anchor sang thứ Bảy,
  trong khi history mô hình hóa weekend ở khoảng 43% weekday volume. Static
  snapshot sau đó vượt freshness threshold.
- Recovery evidence: reset chọn 252 rows theo same-weekday median; anomaly
  `False`, score `0.03`; order freshness `5.0` phút; KB freshness `10.0` phút;
  order/KB contract `0` failures; dbt `21/21`; public tests `10/10`; extended
  tests `21/21`. Fixture healthy dùng timestamp tương đối để freshness test
  không phụ thuộc ngày chạy.
- Fault evidence: duplicate key tạo critical failure; volume drop tạo MAD
  score `10.23`; stale KB tạo freshness `190.0` phút và SLO breach.
- Action: Hoàn thiện `reports/incident_report.md`, automatic quarantine,
  KB freshness/SLO, robust distribution và embedding-norm drift.
- Test hygiene: giữ đúng 10 test public, chỉ ổn định timestamp của fixture
  healthy; gom 21 regression tests của học viên vào một file riêng tại
  `tests_extended/test_submission_regressions.py`.
