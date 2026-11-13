# Marketplace Auto export_v1

`/export/v1` is the machine-only, read-only source for `ma-bridge`. It is not an
employee service and does not accept Marketplace Auto JWTs.

## Contract

- `GET /export/v1/datasets` lists the nine v1 datasets: `sellers`, `products`,
  `orders`, `sales_rows`, `prices`, `ad_campaigns`, `ad_stats_daily`, `stocks`,
  `ratings`.
- `GET /export/v1/{dataset}?cursor=…&limit=…` returns 1–5000 rows ordered by the
  source write timestamp and a deterministic primary-key representation.
- The signed opaque cursor is bound to the dataset and survives export-token
  rotation. Replaying a cursor is safe; an empty page returns the incoming
  cursor unchanged.
- Money is emitted as integer kopecks, percentages as integer basis points or
  whole percentages, marketplace dates as `YYYY-MM-DD`, timestamps as UTC `Z`.
- `seller_id` is always the stable UUID from `wb_core.sellers.id`. Names are
  labels only. `seller_tax_rates` is intentionally not exported: the receiving
  service owns formal names and tax settings.
- Ads are currently emitted with `placement: "all"`; the source mirror has no
  search/recommendations split yet. Orders have the platform retention depth
  configured for `wb_turnover` (currently about 45 days).

## Security boundaries

1. Host nginx admits `/export/v1/` only from `EXPORT_ALLOWED_IP` (vm-bridge).
2. The application repeats the check against `EXPORT_ALLOWED_IPS` (IP/CIDR).
3. A separate bearer token must match `EXPORT_TOKEN` or `EXPORT_TOKEN_NEXT` in
   constant time. Empty token slots disable the endpoint with 503.
4. Every page query begins with `SET TRANSACTION READ ONLY`. The export adapter
   only exposes SELECT operations and never reads marketplace credentials.

Rotation: populate `EXPORT_TOKEN_NEXT`, deploy, switch ma-bridge, verify the new
slot from bridge logs/status, then remove the old `EXPORT_TOKEN`. Never put a
token in logs, issue text, client configuration, or command output.

## Observability hand-off (KRB-41)

Each successful page emits `export_v1_page` with only `dataset`, `rows`,
`bytes`, `has_more`, `duration_ms`, `status`, and an opaque `transfer_id`.
The same boundary records per-seller rows/bytes/pages and a cursor fingerprint
through `ExportObservability`; mixed-seller pages are split after serialization.
Failed active-dataset requests record bounded HTTP status/error-code metrics.
Raw cursors, business rows and tokens are never logged or used as labels.

`GET /export/v1/observability/metrics` and `/status` use the same IP/token guard
as data pages. Status stays 503 until the bridge production-volume preflight is
real, mock-free and reconciled for all nine datasets.

## Rollout and rollback

1. Take a PostgreSQL backup and preserve the previous Git revision.
2. Add the two export-token slots and both vm-bridge allow-list variables to
   the production environment without printing their values.
3. Deploy normally. The three schema migrations build cursor indexes with
   `CREATE INDEX CONCURRENTLY`, so collectors remain writable.
4. Render/reload host nginx, verify `/api/v1/health/ready`, verify that an
   unallowed source receives 403, then verify all nine datasets from vm-bridge.
5. Start ma-bridge on a clean database volume. Keep the former mock volume for
   rollback; do not merge mock sellers with production UUIDs.
6. Reconcile each real `seller_id` UUID with the existing legal entity/cabinet
   before backfill. Do not overwrite the entity's formal name or tax settings.

Rollback switches ma-bridge back to its preserved volume/config, restores the
previous application revision, renders nginx from that revision, and reloads
it. The index migrations are additive and may remain in place.
