-- Returns a row (= test failure) if any dimension lost or invented rows: loaded + quarantined must equal staged.
select 'dim_product' as dimension from (
    select (select count(*) from {{ ref('dim_product') }}) + (select count(*) from {{ ref('quarantine_dim_product') }}) as n,
           (select count(*) from {{ ref('stg_products') }}) as t) where n <> t
union all
select 'dim_customer' from (
    select (select count(*) from {{ ref('dim_customer') }}) + (select count(*) from {{ ref('quarantine_dim_customer') }}) as n,
           (select count(*) from {{ ref('stg_customers') }}) as t) where n <> t
