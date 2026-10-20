"""Databricks job template: load a raw CSV (ADLS Gen2 / UC Volume) into a Delta bronze table (all STRING columns).
UNTESTED template. Parameters (job widgets): catalog, schema, table, path.

    dbutils.widgets.text("catalog", ""); dbutils.widgets.text("schema", ""); ...
"""
import re

IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def run(spark, catalog: str, schema: str, table: str, path: str) -> int:
    for v in (catalog, schema, table):
        if not IDENT.match(v):
            raise ValueError(f"Unsafe identifier: {v!r}")
    df = (spark.read.option("header", True).option("inferSchema", False)  # keep everything as STRING (bronze)
          .option("multiLine", True).csv(path))
    df = df.toDF(*[re.sub(r"[^a-z0-9]+", "_", c.lower()).strip("_") or "col" for c in df.columns])
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}`")
    (df.write.format("delta").mode("overwrite").option("overwriteSchema", True)
       .saveAsTable(f"`{catalog}`.`{schema}`.`{table}`"))
    return df.count()


if __name__ == "__main__":  # on Databricks: spark and dbutils are provided
    w = dbutils.widgets  # noqa: F821
    print(run(spark, w.get("catalog"), w.get("schema"), w.get("table"), w.get("path")))  # noqa: F821
