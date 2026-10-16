-- Gold: grain = one row per order. Rows failing key/date/amount are excluded here and
-- reported by the quarantine model so counts reconcile.
select * from {{ ref('stg_orders') }}
where order_id is not null and order_date is not null and net_sales is not null
