# Grafana dashboards

Four self-contained Grafana dashboards for the EQ2 Companion app. Each is
a standalone JSON export — drop into Grafana Cloud (or any Grafana
instance) via the **+ → Import** flow.

| File | Title | Focus |
|---|---|---|
| `main-highlights.json` | **EQ2 Companion — Main Highlights** | Product-level: total parses, claims, raid content, traffic |
| `databases.json` | **EQ2 Companion — Databases** | Per-DB on-disk size, row counts, server-error rates |
| `frontend.json` | **EQ2 Companion — Front End** | HTTP traffic, latency, page views per user |
| `census.json` | **EQ2 Companion — Census** | Daybreak Census liveness, throughput, cache hit ratio |

## Import procedure

For each file:

1. Grafana → **+ (sidebar)** → **Import dashboard**.
2. Paste the JSON contents (or upload the file).
3. When prompted, pick your Prometheus data source (the one `alloy` is
   writing to — see `alloy/config.alloy` for the remote-write target).
4. **Save**.

All four are independent — you can import any subset without breaking
the others. They share the metrics namespace but no panel-level
dependencies.

## Metric coverage

Every panel pulls from metrics defined in `web/metrics.py`. If a panel
shows "No data" persistently:

- Confirm `alloy` is scraping `/metrics` on the app
  (`prometheus.scrape "eq2app"` target in `alloy/config.alloy`).
- Check the metric exists by curling the app:
  `curl https://<your-app>/metrics | grep <metric_name>`.

Some metrics are bumped by app events that take time to fire (e.g.
`census_health_status` updates every 5 min, `cache_size` only on
cache reads). Wait one scrape interval (~30 s) for first-paint values.

## Adding a new dashboard

1. Copy one of the existing files as a template.
2. Change the `title`, `uid`, `description`, and `tags`.
3. Reset `id: null` and `version: 1`.
4. Drop in your panels (each panel needs its own `id` within the file).
5. Import via the same flow above.
