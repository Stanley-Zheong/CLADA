"""
Data pipeline / ETL DSL.
Adds source/sink/transform pipeline definitions with data quality constraints.
"""

DATA_PIPELINE_DOMAIN = {
    "name": "data_pipeline",
    "description": "Data pipelines / ETL — sources, transforms, sinks with quality SLAs",
    "keywords": ["pipeline", "source", "transform", "sink", "requirement", "invariant", "test"],
    "extra_invariant_types": {
        "freshness": "数据新鲜度: data available within SLA window",
        "completeness": "数据完整性: >= 99.9% events delivered",
        "schema_contract": "Schema 契约: producer and consumer agree on schema",
        "deduplication": "去重: exactly-once or at-least-once with idempotency",
    },
    "phases": {
        "bootstrap": {
            "description": "Data pipeline bootstrap",
            "required_fields": ["pipelines", "requirements"],
            "template": """(domain data/etl)
(meta
  title: "<project-name>"
  platform: "<kafka>" "<clickhouse>")

(pipeline main-pipeline
  source kafka topic: "<topic>"
  transform
    step: "validate-schema" schema: "<schema.avsc>"
    step: "enrich" field: "<field>"
  sink clickhouse table: "<table>"
  constraint
    freshness: "<= 60s"
    completeness: ">= 99.9%")

(invariant INV-DATA-01
  description: "数据完整性 >= 99.9%"
  enforcement: hard
  check: "python validate_completeness.py")
""",
        },
    },
}
