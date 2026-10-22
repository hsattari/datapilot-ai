select customer_key, segment from {{ ref('stg_customers') }} where customer_key is null or rn > 1
