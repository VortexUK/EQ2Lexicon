# Grafana dashboards

Four self-contained Grafana dashboards for the EQ2 Companion app. Each is
a standalone JSON export — drop into Grafana Cloud (or any Grafana
instance) via the **+ → Import** flow.

| File | Title | Focus |
|---|---|---|
| `main-highlights.json` | **EQ2 Companion — Main Highlights** | Product-level: content totals, raid data, a health strip (Census, stale loops, 5xx, active users), traffic |
| `databases.json` | **EQ2 Companion — Databases** | Supabase Postgres: schema sizes, connection-pool saturation, row counts, rankings rebuilds, parse-detail view ages, server errors |
| `frontend.json` | **EQ2 Companion — Front End** | HTTP traffic, latency, slow-request counts per route, event-loop lag, background-loop liveness, SSE subscribers |
| `census.json` | **EQ2 Companion — Census** | Daybreak Census liveness, throughput, latency, share of reads served without a Census call |

## Import procedure

For each file:

1. Grafana → **+ (sidebar)** → **Import dashboard**.
2. Paste the JSON contents (or upload the file).
3. When prompted, pick your Prometheus data source (the one `alloy` is
   writing to — see `../../alloy/config.alloy` for the remote-write target).
4. **Save**.

The `uid` of each dashboard is fixed, so re-importing a newer copy of a
file updates the existing dashboard in place: Grafana warns that a
dashboard with that uid already exists and offers to overwrite it.
Accept — the previous version stays in the dashboard's version history.

All four are independent — you can import any subset without breaking
the others. They share the metrics namespace but no panel-level
dependencies.

## Metric coverage

Every panel pulls from metrics defined in `backend/server/metrics.py`
(scraped by `alloy` as `job="eq2companion"`). If a panel shows "No data"
persistently:

- Confirm `alloy` is scraping `/metrics` on the app
  (`prometheus.scrape "eq2app"` target in `../../alloy/config.alloy`),
  with the `METRICS_TOKEN` bearer it expects.
- Check the metric exists by curling the app:
  `curl -H "Authorization: Bearer $METRICS_TOKEN" https://<your-app>/metrics | grep <metric_name>`.

Some metrics take time to appear:

- `census_health_status` updates every 5 min; `cache_size` only on cache
  reads; wait one scrape interval (~30 s) for first-paint values.
- `background_loop_last_success_timestamp_seconds` only carries the
  singleton loops (refresh queue, parse cleanup, raid live, xpac
  rollover, recruitment sweep) from the process holding the leader
  lease — a standby container during a deploy overlap shows just the
  per-process loops.
- `rankings_rebuild_seconds` fills on the first kills-dataset rebuild
  after a deploy (first `/api/rankings` hit per world).
- `parse_detail_views_total` is bumped per parse-detail page load; the
  24 h panel needs a day of traffic to mean anything.

### Reading the latency panels

`http_request_duration_seconds` has buckets 0.01 / 0.05 / 0.1 / 0.25 / 0.5
/ 0.75 / 1 / 1.5 / 2.5 / 5 / 10 / 30 / 60 s. `histogram_quantile`
interpolates inside a bucket, so with low traffic a single slow request
reads as a plateau at a bucket-derived value for the whole 5 m rate
window. The p95 panels are kept for trend-spotting; the **Slow requests**
row on the Front End dashboard counts actual requests over 250 ms / 1 s
per route and is the honest view. The Databases dashboard's **Postgres
round trip** stat is the unit a DB-only route's floor is measured in.

## Adding a new dashboard

1. Copy one of the existing files as a template.
2. Change the `title`, `uid`, `description`, and `tags`.
3. Reset `id: null` and `version: 1`.
4. Drop in your panels (each panel needs its own `id` within the file).
5. Import via the same flow above.
