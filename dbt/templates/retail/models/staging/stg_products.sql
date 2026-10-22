-- Silver: trimmed product list; rn > 1 marks repeated keys (first category alphabetically is kept).
with s as (
    select nullif(trim(product), '') as product_key, nullif(trim(category), '') as category
    from {{ source('raw_sales', 'products') }}
)
select *, row_number() over (partition by product_key order by category asc nulls last) as rn from s
