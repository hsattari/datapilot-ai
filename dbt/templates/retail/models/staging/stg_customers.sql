with s as (
    select nullif(trim(customer_id), '') as customer_key, nullif(trim(segment), '') as segment
    from {{ source('raw_sales', 'customers') }}
)
select *, row_number() over (partition by customer_key order by segment asc nulls last) as rn from s
