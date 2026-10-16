-- Silver: typed, trimmed. Adapt casts/syntax to the target warehouse (DuckDB shown).
-- Negative net_sales are valid refunds and are NOT filtered here.
select
    nullif(trim(order_id), '')                                  as order_id,
    try_cast(nullif(trim(order_date), '') as date)              as order_date,
    try_cast(nullif(trim(net_sales), '') as decimal(18, 2))     as net_sales,
    try_cast(nullif(trim(cost), '') as decimal(18, 2))          as cost_of_goods_sold,
    nullif(trim(product), '')                                   as product,
    nullif(trim(region), '')                                    as region,
    nullif(trim(customer), '')                                  as customer
from {{ source('raw_sales', 'orders') }}
