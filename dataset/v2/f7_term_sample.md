# F7: 30 random (term, definition) pairs

| term | shape | definition | previous (F2) name |
|---|---|---|---|
| Band-N log record | mixed | rows where `env` is `prod` and either `normalized_weight` is above 0.4 or `service_name` is `billing` | deploy-window log record |
| other-currency-code transaction | not_in | rows where `currencyCode` is not `GBP` | first-time transaction |
| heavy-machinery sensor reading | value_list | rows where `machine_class` is one of `conveyor`, `welder`, `kiln` | drifting sensor reading |
| high-fee-accrued loan | threshold | rows where `lateFeeAccrued` is greater than 4 | hefty loan |
| Basket-M meter reading | value_list | rows where `tariff` is one of `flat`, `dynamic` | peak-hour meter reading |
| submitted-to-resolved time | derived | the elapsed time from `submitted_ts` to `resolved_ts` in seconds: (`resolved_ts` - `submitted_ts`), where both columns are epoch seconds | elapsed time |
| Category-F claim | value_list | rows where `policy_type` is one of `renters`, `auto` | subrogation claim |
| target-list workout | or_cols | rows where `activityType` is `weightlifting` or `intensityLevel` is `moderate` | gym workout |
| pastry order | value_list | rows where `cuisine` is `bakery` | promo order |
| property-type-series listing event | like_prefix | rows where `propertyType` starts with `sing` | new-build listing event |
| Bucket-M product | value_list | rows where `brand` is one of `acme`, `globex` | private-label product |
| high-response API request | threshold | rows where `response_bytes` is over 270150 | extreme API request |
| commercial-vehicle vehicle sample | value_list | rows where `vehicleClass` is one of `van`, `rigid`, `refuse` | off-route vehicle sample |
| Basket-C claim | or_cols | rows where `adjuster_tier` is `mid_level` or `sla_breached_flag` is 0 | litigated claim |
| non-technical candidate event | value_list | rows where `department` is one of `marketing`, `legal` | re-engaged candidate event |
| dispatch-to-arrived time | derived | the elapsed time from `dispatch_at` to `arrived_at` in minutes: (`arrived_at` - `dispatch_at`) / 60000, where both columns are epoch milliseconds | cycle time |
| reference-set workout | or_cols | rows where `completion_status` is `aborted` or `is_test_record` is 0 | interval workout |
| other-meter-id meter reading | not_in | rows where `meter_id` is not one of `m-0049`, `m-0014` | zero-usage meter reading |
| content-type-family edge request | like_prefix | rows where `contentType` starts with `imag` | regional-spike edge request |
| target-list warranty claim | value_list | rows where `warrantyTier` is `extended` | express-swap warranty claim |
| high-inspection-duration inspection | threshold | rows where `inspection_duration_m` is above 15 | top-tier inspection |
| Bucket-D prediction | or_cols | rows where `outcome` is `timeout` or `sla_breached_flag` is 0 | fallback prediction |
| shortlist turbine reading | value_list | rows where `blade_pitch_mode` is `feathered` | high-wind turbine reading |
| Cohort-5 alert | or_cols | rows where `rule_name` is `mass_download` or `analyst_verdict` is `pending` | noisy alert |
| Tier-S turbine reading | or_cols | rows where `farm_location` is `north_sea_offshore` or `is_deleted` is 0 | flagship turbine reading |
| Family-8 stock movement | value_list | rows where `warehouseCode` is one of `wh-bir`, `wh-man`, `wh-bri` | inter-site stock movement |
| high-rotor-rpm turbine reading | threshold | rows where `rotor_rpm` is greater than 15 | hefty turbine reading |
| flight-status-series flight leg | like_prefix | rows where `flightStatus` starts with `in_a` | hub-bound flight leg |
| Kind-1 enrolment | value_list | rows where `academic_faculty` is one of `science`, `law` | overload enrolment |
| recently active user | time_active | a `userId` that has at least one row in the last 60 days | recurring user |
