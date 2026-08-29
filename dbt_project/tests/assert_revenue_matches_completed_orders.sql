-- Business invariant: the daily revenue mart must equal the sum of completed
-- orders at the same grain. This catches silent join multiplication even when
-- the SQL model itself executes successfully.
with expected as (
    select
        order_date,
        count(*) as expected_completed_order_rows,
        sum(amount_usd) as expected_daily_revenue
    from {{ ref('stg_orders') }}
    where status = 'completed'
    group by 1
),
mismatches as (
    select
        coalesce(actual.order_date, expected.order_date) as order_date,
        actual.completed_order_rows,
        expected.expected_completed_order_rows,
        actual.daily_revenue,
        expected.expected_daily_revenue
    from {{ ref('fct_daily_revenue') }} as actual
    full outer join expected
        on actual.order_date = expected.order_date
    where actual.order_date is null
       or expected.order_date is null
       or actual.completed_order_rows != expected.expected_completed_order_rows
       or abs(actual.daily_revenue - expected.expected_daily_revenue) > 0.000001
)
select *
from mismatches
