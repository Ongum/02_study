# dbt model layer — planned, not built

Use staging for source normalization, intermediate for shared conforming logic, domain-specific marts for analytics consumption. Preserve counts and grains. Keep tests on primary keys, uniqueness, accepted values, non-null fields, and source freshness once the connection and raw schema are verified. Choose the dbt adapter and deployment strategy during stage 04–07.

The verified local prototype and proposed table/partition/test mapping are
documented in `docs/contracts/bigquery_dbt_migration.md`. This folder still does
not contain a configured dbt project and no BigQuery connection was attempted.
