"""Dimension tables (products, customers) that sales can be joined to.

Each dimension has a unique key (`*_key`) and one attribute used for grouping. Duplicate or blank keys are
quarantined at build time, so a join can never multiply fact rows. Facts without a matching dimension row are
kept and reported as 'Unassigned' (LEFT JOIN), so totals always reconcile with the fact table.
"""
DIMS = {
    "dim_product": {
        "table": "dim_product", "label": "Products", "alias": "p",
        "fact_col": "product", "key_canon": "product", "attr_canon": "category",
        "key_col": "product_key", "attr_col": "category",
        "key_syn": ["product", "product_name", "product_id", "product_code", "sku", "item"],
        "attr_syn": ["category", "product_category", "family", "product_family", "department", "product_group"],
    },
    "dim_customer": {
        "table": "dim_customer", "label": "Customers", "alias": "c",
        "fact_col": "customer", "key_canon": "customer", "attr_canon": "segment",
        "key_col": "customer_key", "attr_col": "segment",
        "key_syn": ["customer", "customer_id", "customer_name", "customer_code", "client", "client_id"],
        "attr_syn": ["segment", "customer_segment", "tier", "customer_tier", "customer_group", "channel"],
    },
}
# Plan dimension/filter name -> (dimension table role)
ATTR_TO_ROLE = {d["attr_col"]: role for role, d in DIMS.items()}


def mapping_columns(role: str) -> tuple[str, str]:
    d = DIMS[role]
    return d["key_canon"], d["attr_canon"]
