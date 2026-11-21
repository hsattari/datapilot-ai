{% snapshot dim_customer_history %}
{{ config(target_schema='snapshots', unique_key='customer_key', strategy='check', check_cols=['segment']) }}
-- Keeps every segment a customer has had, so segment changes are traceable over time.
select customer_key, segment from {{ ref('dim_customer') }}
{% endsnapshot %}
