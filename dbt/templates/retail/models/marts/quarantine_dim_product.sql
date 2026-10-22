select product_key, category from {{ ref('stg_products') }} where product_key is null or rn > 1
