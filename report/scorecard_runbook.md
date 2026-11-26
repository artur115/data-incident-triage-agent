# Triage scorecard: runbook

- Diagnosis fully correct (category, root cause and action): **24/48**
- Real problems closed: **0**  ·  bad reloads: **0**
- Pages to people: 35 (of which unneeded: 9)

| Template | Incidents | Correct |
|---|---:|---:|
| failed_run | 12 | 12 |
| late_load | 9 | 9 |
| nullable_column | 3 | 0 |
| quiet_sunday | 6 | 0 |
| ran_before_parent | 4 | 0 |
| vendor_null_keys | 7 | 3 |
| vendor_timezone | 7 | 0 |

| Incident | Template | Truth | Got | Root cause (got) | Action | What happened |
|---|---|---|---|---|---|---|
| INC-001 | late_load | late_data | late_data | analytics.stg_orders | close | closed |
| INC-002 | late_load | late_data | late_data | analytics.fct_daily_revenue | close | closed |
| INC-003 | late_load | late_data | late_data | analytics.dim_customers | close | closed |
| INC-004 | late_load | late_data | late_data | analytics.stg_orders | close | closed |
| INC-005 | late_load | late_data | late_data | analytics.fct_daily_revenue | close | closed |
| INC-006 | late_load | late_data | late_data | analytics.dim_customers | close | closed |
| INC-007 | late_load | late_data | late_data | analytics.stg_orders | close | closed |
| INC-008 | late_load | late_data | late_data | analytics.fct_daily_revenue | close | closed |
| INC-009 | late_load | late_data | late_data | analytics.dim_customers | close | closed |
| INC-010 | quiet_sunday | false_alert | upstream_break ✗ | analytics.stg_orders | escalate | paged |
| INC-011 | quiet_sunday | false_alert | upstream_break ✗ | analytics.fct_daily_revenue | escalate | paged |
| INC-012 | quiet_sunday | false_alert | upstream_break ✗ | analytics.fct_email_engagement | escalate | paged |
| INC-013 | quiet_sunday | false_alert | upstream_break ✗ | analytics.stg_orders | escalate | paged |
| INC-014 | quiet_sunday | false_alert | upstream_break ✗ | analytics.fct_daily_revenue | escalate | paged |
| INC-015 | quiet_sunday | false_alert | upstream_break ✗ | analytics.fct_email_engagement | escalate | paged |
| INC-016 | nullable_column | false_alert | upstream_break ✗ | analytics.stg_orders | escalate | paged |
| INC-017 | nullable_column | false_alert | upstream_break ✗ | analytics.stg_orders | escalate | paged |
| INC-018 | nullable_column | false_alert | upstream_break ✗ | analytics.stg_orders | escalate | paged |
| INC-019 | failed_run | real_break | real_break | analytics.stg_orders | reload | approval_requested |
| INC-020 | failed_run | real_break | real_break | analytics.fct_daily_revenue | reload | approval_requested |
| INC-021 | failed_run | real_break | real_break | analytics.fct_email_engagement | reload | reloaded |
| INC-022 | failed_run | real_break | real_break | analytics.stg_orders | reload | approval_requested |
| INC-023 | failed_run | real_break | real_break | analytics.fct_daily_revenue | reload | approval_requested |
| INC-024 | failed_run | real_break | real_break | analytics.fct_email_engagement | reload | reloaded |
| INC-025 | failed_run | real_break | real_break | analytics.stg_orders | reload | approval_requested |
| INC-026 | failed_run | real_break | real_break | analytics.fct_daily_revenue | reload | approval_requested |
| INC-027 | failed_run | real_break | real_break | analytics.fct_email_engagement | reload | reloaded |
| INC-028 | failed_run | real_break | real_break | analytics.stg_orders | reload | approval_requested |
| INC-029 | failed_run | real_break | real_break | analytics.fct_daily_revenue | reload | approval_requested |
| INC-030 | failed_run | real_break | real_break | analytics.fct_email_engagement | reload | reloaded |
| INC-031 | ran_before_parent | real_break | upstream_break ✗ | analytics.fct_daily_revenue | escalate | paged |
| INC-032 | ran_before_parent | real_break | upstream_break ✗ | analytics.fct_daily_revenue | escalate | paged |
| INC-033 | ran_before_parent | real_break | upstream_break ✗ | analytics.fct_daily_revenue | escalate | paged |
| INC-034 | ran_before_parent | real_break | upstream_break ✗ | analytics.fct_daily_revenue | escalate | paged |
| INC-035 | vendor_timezone | upstream_break | upstream_break ✗ | analytics.stg_orders | escalate | paged |
| INC-036 | vendor_timezone | upstream_break | upstream_break ✗ | analytics.stg_orders | escalate | paged |
| INC-037 | vendor_timezone | upstream_break | upstream_break ✗ | analytics.stg_orders | escalate | paged |
| INC-038 | vendor_timezone | upstream_break | upstream_break ✗ | analytics.stg_orders | escalate | paged |
| INC-039 | vendor_timezone | upstream_break | upstream_break ✗ | analytics.stg_orders | escalate | paged |
| INC-040 | vendor_timezone | upstream_break | upstream_break ✗ | analytics.stg_orders | escalate | paged |
| INC-041 | vendor_timezone | upstream_break | upstream_break ✗ | analytics.stg_orders | escalate | paged |
| INC-042 | vendor_null_keys | upstream_break | upstream_break ✗ | analytics.fct_daily_revenue | escalate | paged |
| INC-043 | vendor_null_keys | upstream_break | upstream_break | raw.payments | escalate | paged |
| INC-044 | vendor_null_keys | upstream_break | upstream_break ✗ | analytics.fct_daily_revenue | escalate | paged |
| INC-045 | vendor_null_keys | upstream_break | upstream_break | raw.payments | escalate | paged |
| INC-046 | vendor_null_keys | upstream_break | upstream_break ✗ | analytics.fct_daily_revenue | escalate | paged |
| INC-047 | vendor_null_keys | upstream_break | upstream_break | raw.payments | escalate | paged |
| INC-048 | vendor_null_keys | upstream_break | upstream_break ✗ | analytics.fct_daily_revenue | escalate | paged |
