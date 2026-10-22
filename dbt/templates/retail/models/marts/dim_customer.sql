select customer_key, segment from {{ ref('stg_customers') }} where customer_key is not null and rn = 1
