-- One row per product key. Joined from fact_sales with LEFT JOIN, so unmatched sales stay visible as Unassigned.
select product_key, category from {{ ref('stg_products') }} where product_key is not null and rn = 1
