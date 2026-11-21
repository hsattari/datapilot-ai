{% snapshot dim_product_history %}
{{ config(target_schema='snapshots', unique_key='product_key', strategy='check', check_cols=['category']) }}
-- Keeps every category a product has had (dbt_valid_from / dbt_valid_to), so a re-categorisation never rewrites history.
select product_key, category from {{ ref('dim_product') }}
{% endsnapshot %}
