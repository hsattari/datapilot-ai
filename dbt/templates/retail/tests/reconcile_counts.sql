-- Fails (returns a row) if loaded + quarantined != staged: no silent record loss.
select 1
from (
    select
        (select count(*) from {{ ref('fact_sales') }})
      + (select count(*) from {{ ref('quarantine_sales') }}) as combined,
        (select count(*) from {{ ref('stg_orders') }})       as staged
) c
where combined <> staged
