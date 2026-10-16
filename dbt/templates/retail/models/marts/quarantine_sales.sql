select * from {{ ref('stg_orders') }}
where order_id is null or order_date is null or net_sales is null
