# Kubernetes Cluster Architecture

This document explains how the cSVD Dashboard platform runs on Kubernetes. It covers every
component in the Helm chart, how they connect, and how data moves through the system.

If you are new to Kubernetes, think of the cluster as a building where each component is a
tenant with its own room, address, and set of keys. The Helm chart is the blueprint that
describes every room, every lock, and every hallway.

---

## Table of Contents

- [Overview](#overview)
- [Architecture Diagram](#architecture-diagram)
- [Components](#components)
  - [Dashboard (Deployment)](#dashboard-deployment)
  - [PostgreSQL (StatefulSet)](#postgresql-statefulset)
  - [Pipeline (CronJob)](#pipeline-cronjob)
  - [ntfy (Deployment, optional)](#ntfy-deployment)
  - [Healthchecks (Deployment, optional)](#healthchecks-deployment)
  - [Observability Stack](#observability-stack)
  - [Monitoring Stack](#monitoring-stack)
- [How Data Flows](#how-data-flows)
- [Shared Storage](#shared-storage)
- [Networking](#networking)
- [Security](#security)
- [Configuration](#configuration)
- [Feature Flags](#feature-flags)
- [Glossary](#glossary)

---

## Overview

The Helm chart deploys a self-contained research platform with six concerns:

| Concern | What It Does | Kubernetes Resource |
| --------- | ------------- | --------------------- |
| **Dashboard** | Interactive R Shiny web app for exploring up-to-date cSVD research data | Deployment |
| **Database** | PostgreSQL instance that stores extracted gene records | StatefulSet |
| **Pipeline** | Weekly Python ETL job: PubMed search, LLM extraction, data loading | CronJob |
| **Notifications** | Push alerts (ntfy) and uptime monitoring (Healthchecks) | Deployments |
| **Observability** | Metrics (Prometheus + Grafana) and logs (VictoriaLogs + Vector) | Subcharts |
| **Monitoring** | Blackbox probes, metrics exporters, ServiceMonitors for Prometheus scraping | Various |
| **Networking** | Ingress routing, internal services, optional network policies | Ingress, Services |

Everything is driven by a single `values.yaml` file. Change a value, run `helm upgrade`,
and the cluster converges to the new state.

---

## Architecture Diagram

```mermaid
graph TD
    ingress["Ingress Controller<br/>(nginx)"]

    ingress -- "shiny.local" --> dashboard["Dashboard<br/>(port:3838)"]
    ingress -- "grafana.local" --> grafana["Grafana<br/>(port:80)"]
    ingress -- "ntfy.local" --> ntfy["ntfy<br/>(port:80)"]
    ingress -- "healthchecks.local" --> healthchecks["Healthchecks<br/>(port:8000)"]
    ingress -- "prometheus.local<br/>(when enabled)" --> prometheus
    ingress -- "alertmanager.local<br/>(when enabled)" --> alertmanager["Alertmanager<br/>(port:9093)"]
    ingress -- "victoria-logs.local<br/>(when enabled)" --> victorialogs

    dashboard -- "reads QS files" --> qspvc[("QS Data PVC<br/>1Gi")]
    pipeline["Pipeline CronJob"] -- "writes QS files" --> qspvc
    pipeline -- "reads / writes" --> postgresql[("PostgreSQL<br/>(port:5432)<br/>+ postgres_exporter sidecar<br/>(port:9187)<br/>10Gi PVC")]

    grafana -. "scrapes metrics" .-> prometheus["Prometheus"]
    grafana -. "queries logs" .-> victorialogs["VictoriaLogs"]

    blackbox["Blackbox Exporter<br/>(port:9115)"]
    blackbox -. "probes HTTP" .-> dashboard
    blackbox -. "probes HTTP" .-> healthchecks

    prometheus -. "scrapes<br/>(port:9187)" .-> postgresql
    prometheus -. "scrapes /metrics" .-> ntfy
    prometheus -. "scrapes<br/>(port:9115)" .-> blackbox
    prometheus -. "scrapes<br/>(port:10254)" .-> ingress
```

---

## Components

### Dashboard (Deployment)

**Analogy**: The dashboard is the storefront -- the only part that visitors see. It reads
from a shelf of pre-built data files (QS files) and never talks to the database directly.

**Template**: `templates/dashboard-deployment.yaml`

| Property | Value |
| ---------- | ------- |
| Image | `rshiny-dashboard:2.0.0` |
| Port | 3838 |
| Runs as user | 997 (non-root), fsGroup 997 |
| Replicas | 1 (configurable) |
| CPU | 1000m request / 4000m limit |
| Memory | 2Gi request / 4Gi limit |

**Startup sequence**:

1. An init container (`fix-qs-permissions`, `busybox:1.37.0`) runs as root to `chown` the
   QS data directory to uid 997, so the dashboard process can read it.
2. The main container starts and loads QS files from `/srv/shiny-server/data/qs`.
3. Startup probe allows up to ~10 minutes for the app to initialize (pre-computing
   tooltip HTML, building fastmap indices, optionally preloading Table 2).

**Environment**:

| Variable | Source | Purpose |
| ---------- | -------- | --------- |
| `PRELOAD_TABLE2` | values.yaml | Controls whether Table 2 loads at startup or lazily |

**Volumes**:

| Mount Path | Source | Access |
| ------------ | -------- | -------- |
| `/srv/shiny-server/data/qs` | QS Data PVC | Read/Write |
| `/srv/shiny-server/.Renviron` | `.Renviron` Secret (subPath) | Read-Only |

**Health checks** (all `GET /` on port 3838):

| Probe | Purpose |
| ------- | --------- |
| Startup | Allows up to ~10 minutes for data preparation before the pod is killed |
| Liveness | Periodic liveness check |
| Readiness | Periodic readiness check |

---

### PostgreSQL (StatefulSet)

**Analogy**: The database is the filing cabinet. The pipeline puts records in; the QS
generation script reads them out. The dashboard never opens this cabinet directly.

**Template**: `templates/postgresql-statefulset.yaml`

| Property | Value |
| ---------- | ------- |
| Image | `postgres:18` |
| Port | 5432 |
| Runs as user | 999 (postgres), fsGroup 999 |
| Replicas | 1 (single-instance, no HA) |
| CPU | 500m request / 750m limit |
| Memory | 512Mi request / 768Mi limit |
| Storage | 10Gi PVC (auto-provisioned by StatefulSet) |

**Why a StatefulSet?** Unlike a Deployment, a StatefulSet gives the database a stable
network identity (`postgresql-0`) and a persistent volume that survives pod restarts. If the
pod is evicted and rescheduled, it reattaches to the same 10Gi disk.

**Automatic schema initialization**: On first startup, PostgreSQL runs SQL scripts mounted
from a ConfigMap (`postgresql-initdb-configmap`) into `/docker-entrypoint-initdb.d/`:

| Order | File | Contents |
| ------- | ------ | ---------- |
| 1 | `01-setup.sql` | Core tables (genes, clinical_trials, pubmed_refs), indices, triggers |
| 2 | `02-external-tables.sql` | Cache tables (ncbi_gene_info, uniprot_info, pubmed_citations) |

These scripts are embedded in the ConfigMap at template render time from the chart's `sql/`
directory.

#### postgres_exporter sidecar

**Condition**: `postgresql.exporter.enabled` (default: `true`)

A sidecar container that connects to the local PostgreSQL instance and exposes database
metrics (connections, queries, replication lag, etc.) for Prometheus scraping.

| Property | Value |
| ---------- | ------- |
| Image | `prometheuscommunity/postgres-exporter:v0.16.0` |
| Port | 9187 (`metrics`) |
| CPU | 250m request / 500m limit |
| Memory | 256Mi request / 512Mi limit |

The exporter reads `DATA_SOURCE_USER` and `DATA_SOURCE_PASS` from the `db-credentials`
secret (using the `POSTGRES_USER` / `POSTGRES_PASSWORD` keys) and builds
`DATA_SOURCE_URI` as `localhost:5432/<database>?sslmode=disable`.

**Health checks**: HTTP liveness and readiness probes on `GET /` of the `metrics`
port (9187). PostgreSQL itself uses `pg_isready` (exec probe) for both liveness and
readiness — not an HTTP check.

**Service**: A headless ClusterIP service (`clusterIP: None`) provides stable DNS:
`<release>-svd-dashboard-postgresql.svd.svc.cluster.local`

The service exposes two ports:

| Port Name | Port | Condition |
| ----------- | ------ | ----------- |
| `postgresql` | 5432 | Always |
| `metrics` | 9187 | When `postgresql.exporter.enabled` is `true` |

---

### Pipeline (CronJob)

**Analogy**: The pipeline is the night-shift worker. Once a week it arrives, does four tasks
in order, and leaves. Each task must finish before the next one starts.

**Template**: `templates/pipeline-cronjob.yaml`

| Property | Value |
| ---------- | ------- |
| Schedule | `0 3 * * 1` (3 AM UTC every Monday) |
| Concurrency | Forbid (no overlapping runs) |
| Deadline | 7200s (2 hours max) |
| Backoff limit | 1 (no retries on failure) |
| History | 3 successful / 3 failed jobs retained |
| Runs as user | 65534 (nobody), group 65534, `seccompProfile: RuntimeDefault` |

The CronJob uses **init containers** for the first three steps (they run sequentially and
must all succeed) and a **regular container** for the final step. Every step runs the
pipeline image (`svd-pipeline:2.0.0`) — the image bundles both Python and `kubectl`, so
no separate tooling image is needed for Step 4.

```mermaid
graph TD
    step1["Step 1: run-pipeline<br/>(init container)"]
    step2["Step 2: sync-external<br/>(init container, conditional)"]
    step3["Step 3: generate-qs<br/>(init container)"]
    step4["Step 4: restart-dashboard<br/>(regular container)"]

    step1 --> step2 --> step3 --> step4
```

#### Step 1 -- Run Pipeline

```bash
python pipeline/main.py --days-back 7
```

Searches PubMed for recent papers, retrieves full text, extracts gene data via Claude LLM,
validates against NCBI Gene, and loads results into PostgreSQL.

- Image: `svd-pipeline:2.0.0`
- Secrets: `db-credentials` (envFrom) + `pipeline-secrets` (envFrom) — this step sees
  the full pipeline-secret surface, including `ANTHROPIC_API_KEY`
- Resources: 750m/1000m CPU, 1Gi/2Gi memory

#### Step 2 -- Sync External Data (conditional)

```bash
python pipeline/main.py --sync-external-data
```

Only runs if `pipeline.syncExternalData` is `true` in values.yaml. Fetches supplementary
data from NCBI Gene, UniProt, and PubMed and caches it in database tables.

- Same image, secrets (full `pipeline-secrets` via envFrom), and resources as Step 1

#### Step 3 -- Generate QS Files

```bash
Rscript scripts/trigger_update.R
```

Reads all tables from PostgreSQL and produces serialized QS files (a fast binary format for
R) on the shared PVC at `/app/data/qs`.

- Secrets: `db-credentials` (envFrom), plus only `NCBI_API_KEY` and `ENTREZ_EMAIL`
  from `pipeline-secrets` (individual `valueFrom` refs) — this step does not see
  `ANTHROPIC_API_KEY` or notification URLs

#### Step 4 -- Restart Dashboard

```bash
kubectl rollout restart deployment/<release>-svd-dashboard-dashboard
```

Triggers a rolling restart of the dashboard Deployment so the new pods pick up the freshly
written QS files.

- Image: `svd-pipeline:2.0.0` (reuses the pipeline image — `kubectl` is preinstalled)
- Resources: 50m/100m CPU, 64Mi/128Mi memory (the kubectl call itself is lightweight)
- Requires RBAC: a dedicated ServiceAccount with `get` and `patch` on `deployments`

**RBAC resources** (in `templates/pipeline-rbac.yaml`):

| Resource | Name |
| ---------- | ------ |
| ServiceAccount | `<release>-svd-dashboard-pipeline` |
| Role | `<release>-svd-dashboard-pipeline` |
| RoleBinding | `<release>-svd-dashboard-pipeline` |

The Role grants only `get` and `patch` on `apps/deployments` -- the minimum needed to
trigger a rollout restart.

---

### ntfy (Deployment)

**Analogy**: ntfy is the megaphone. When the pipeline finishes (or fails), it sends a
push notification here so you know without checking manually.

**Template**: `templates/ntfy-deployment.yaml`
**Condition**: `notifications.ntfy.enabled` (default: `true`)

| Property | Value |
| ---------- | ------- |
| Image | `binwiederhier/ntfy:v2.17.0` |
| Port | 80 |
| CPU | 500m request / 750m limit |
| Memory | 512Mi request / 768Mi limit |
| Storage | 1Gi PVC for message cache |

**Configuration** (set via environment variables):

| Variable | Value |
| ---------- | ------- |
| `NTFY_UPSTREAM_BASE_URL` | `https://ntfy.sh` (from `notifications.ntfy.upstreamBaseUrl`) — forwards iOS pushes to the public ntfy network |
| `NTFY_BASE_URL` | `http(s)://<ingress.hosts.ntfy>` — scheme is chosen from `ingress.tls.enabled` |
| `NTFY_CACHE_FILE` | `/var/cache/ntfy/cache.db` |
| `NTFY_AUTH_DEFAULT_ACCESS` | `deny-all` (configurable via `notifications.ntfy.auth`) |
| `NTFY_BEHIND_PROXY` | `true` |
| `NTFY_ENABLE_METRICS` | `true` (conditional on `notifications.ntfy.metrics.enabled`) |
| `NTFY_ENABLE_SIGNUP` | `false` |

**Health checks**: HTTP liveness and readiness probes on `GET /v1/health` (port 80).

**ntfy ServiceMonitor** (conditional on `notifications.ntfy.metrics.enabled` and
`monitoring.serviceMonitor.enabled`): scrapes `/metrics` on port `http` at 30s intervals.

---

### Healthchecks (Deployment)

**Analogy**: Healthchecks is the attendance sheet. The pipeline pings it on each run. If
a ping is missed, Healthchecks raises an alert -- meaning the pipeline did not run as
scheduled.

**Template**: `templates/healthchecks-deployment.yaml`
**Condition**: `notifications.healthchecks.enabled` (default: `true`)

| Property | Value |
| ---------- | ------- |
| Image | `healthchecks/healthchecks:v4.0` |
| Port | 8000 |
| CPU | 500m request / 750m limit |
| Memory | 512Mi request / 768Mi limit |
| Storage | 1Gi PVC (SQLite database) |
| Requires | `notifications.healthchecks.secretKey` (Django SECRET_KEY) |

Healthchecks uses SQLite (not the cluster's PostgreSQL) for its own data, stored on a
dedicated PVC.

**Configuration** (set via environment variables):

| Variable | Value |
| ---------- | ------- |
| `ALLOWED_HOSTS` | `*, <ingress.hosts.healthchecks>` |
| `APPRISE_ENABLED` | `True` |
| `DB` | `sqlite` |
| `DB_NAME` | `/data/hc.sqlite` |
| `DEBUG` | `False` |
| `DEFAULT_FROM_EMAIL` | `noreply@svd-dashboard.org` |
| `SECRET_KEY` | From the `<release>-svd-dashboard-healthchecks` Secret (key `SECRET_KEY`) |
| `SITE_NAME` | `SVD Pipeline Healthchecks` |
| `SITE_ROOT` | `http(s)://<ingress.hosts.healthchecks>` — scheme is chosen from `ingress.tls.enabled` |

**Health checks**: HTTP liveness and readiness probes on `GET /api/v3/status/`
(port 8000).

**Healthchecks Probe** (conditional on `notifications.healthchecks.enabled`,
`monitoring.blackboxExporter.enabled`, and `monitoring.serviceMonitor.enabled`): A
Prometheus Operator `Probe` resource that uses the Blackbox Exporter to perform HTTP checks
against `http://healthchecks:8000/api/v3/status/` and `http://dashboard:3838/` using the
`http_2xx` module at 30s intervals.

---

### Observability Stack

The observability layer is built from two Helm subcharts and one custom Deployment.

#### Prometheus + Grafana (kube-prometheus-stack ~82.x)

**Condition**: `observability.prometheus.enabled` (default: `true`)

Deploys Prometheus (metrics collection), Grafana (dashboards), and Alertmanager (alert
routing). The Prometheus Operator enables declarative monitoring via ServiceMonitor and
PrometheusRule custom resources.

Namespace-scoped discovery: Prometheus only scrapes ServiceMonitors, PodMonitors, Probes,
and PrometheusRules whose namespace matches the release namespace (matched via
`kubernetes.io/metadata.name` label). This prevents accidental cross-namespace metric
collection. The bundled Grafana is wired to the in-cluster Image Renderer via
`GF_RENDERING_SERVER_URL` / `GF_RENDERING_CALLBACK_URL` and loads the
`victoriametrics-logs-datasource` plugin.

#### VictoriaLogs (victoria-logs-single ~0.x)

**Condition**: `observability.victoriaLogs.enabled` (default: `true`)

Deploys VictoriaLogs for log aggregation with Vector as the log collector. Collects
stdout/stderr from all pods in the namespace.

| Setting | Value |
| --------- | ------- |
| Retention | 30 days |
| Storage | 10Gi PVC |

#### VictoriaLogs Grafana Datasource (ConfigMap)

**Template**: `templates/grafana-victorialogs-datasource.yaml`
**Condition**: `observability.victoriaLogs.enabled`

A sidecar-style datasource ConfigMap labelled `grafana_datasource: "1"` so that the
bundled Grafana (which runs the datasource sidecar from kube-prometheus-stack)
auto-discovers it and registers a `VictoriaMetrics Logs` datasource. Two values drive it:

| Value | Purpose |
| ------- | --------- |
| `observability.victoriaLogs.datasource.uid` | Stable datasource UID used by dashboards |
| `observability.victoriaLogs.datasource.url` | In-cluster URL, e.g. `http://svd-victoria-logs-single-server.svd.svc.cluster.local:9428` |

#### Grafana Image Renderer

**Template**: `templates/grafana-image-renderer-deployment.yaml`
**Condition**: `observability.grafanaImageRenderer.enabled` (default: `true`)

| Property | Value |
| ---------- | ------- |
| Image | `grafana/grafana-image-renderer:v5.5.1` |
| Port | 8081 |
| Runs as user | 472 |
| CPU | 250m request / 1000m limit |
| Memory | 512Mi request / 1Gi limit |

Renders Grafana panels as PNG/PDF images for alert notifications and scheduled reports.
Configured for clustered rendering (`RENDERING_MODE=clustered`,
`RENDERING_CLUSTERING_MAX_CONCURRENCY=2`) with metrics (`ENABLE_METRICS=true`) and
verbose logging enabled.

#### External Grafana (fallback)

**Condition**: `observability.grafana.external.enabled` (default: `false`) **and**
`observability.prometheus.enabled` is `false`

Used only when you disable the in-chart Prometheus subchart but want the Ingress to
still route `grafana.<domain>` to a Grafana instance running elsewhere (e.g. a shared
`monitoring` namespace). The chart creates an `ExternalName` Service that proxies traffic
to that remote Grafana. When the Prometheus subchart is enabled, this resource is
skipped — the Ingress routes directly to the subchart's Grafana Service.

---

### Monitoring Stack

The monitoring layer connects Prometheus to application-level metrics and health probes.
All resources below are conditional on `monitoring.serviceMonitor.enabled` (default: `true`)
and their respective component flags.

#### Blackbox Exporter (Deployment + ConfigMap + Service)

**Templates**: `templates/blackbox-exporter-deployment.yaml`, `templates/blackbox-exporter-configmap.yaml`, `templates/blackbox-exporter-service.yaml`
**Condition**: `monitoring.blackboxExporter.enabled` (default: `true`)

The Blackbox Exporter probes endpoints over HTTP and reports whether they are reachable.
Prometheus executes these checks via `Probe` custom resources.

| Property | Value |
| ---------- | ------- |
| Image | `prom/blackbox-exporter:v0.25.0` |
| Port | 9115 |
| CPU | 250m request / 500m limit |
| Memory | 128Mi request / 256Mi limit |

**ConfigMap**: Defines an `http_2xx` module that probes HTTP/1.1 and HTTP/2.0 endpoints,
expects a 200 status code, follows redirects, and prefers IPv4.

**Health checks**: HTTP liveness and readiness probes on `GET /health` (port 9115).

#### ServiceMonitors

Three ServiceMonitor custom resources tell Prometheus what to scrape:

| ServiceMonitor | Target Port | Path | Interval | Condition |
| ---------------- | ------------- | ------ | ---------- | ----------- |
| PostgreSQL exporter | `metrics` (9187) | `/metrics` | 30s | `postgresql.exporter.enabled` |
| ntfy | `http` (80) | `/metrics` | 30s | `notifications.ntfy.metrics.enabled` |
| Ingress-Nginx | `metrics` (10254) | `/metrics` | 30s | `monitoring.ingressNginx.enabled` |

The Ingress-Nginx ServiceMonitor uses a cross-namespace selector (`ingress-nginx`
namespace) to scrape the controller's built-in metrics endpoint.

#### Healthchecks Probe

**Template**: `templates/healthchecks-probe.yaml`
**Condition**: `notifications.healthchecks.enabled` + `monitoring.blackboxExporter.enabled` + `monitoring.serviceMonitor.enabled`

A Prometheus Operator `Probe` resource that defines blackbox HTTP checks. Targets and
the prober URL are all fully qualified in-cluster DNS names:

| Target | Module |
| -------- | -------- |
| `http://<release>-svd-dashboard-healthchecks.<namespace>.svc.cluster.local:8000/api/v3/status/` | `http_2xx` |
| `http://<release>-svd-dashboard-dashboard.<namespace>.svc.cluster.local:3838/` | `http_2xx` |

The prober itself is addressed as
`<release>-svd-dashboard-blackbox-exporter.<namespace>.svc.cluster.local:9115`. Prometheus
runs the probe at the interval configured in `monitoring.serviceMonitor.interval`
(~30s by default).

#### Ingress-Nginx Metrics Service

**Template**: `templates/ingress-nginx-metrics-service.yaml`
**Condition**: `monitoring.ingressNginx.enabled` (default: `true`)

A headless ClusterIP Service deployed into the `ingress-nginx` namespace that exposes
port 10254 on the nginx controller pods. This Service is the scrape target for the
Ingress-Nginx ServiceMonitor.

---

## How Data Flows

The full data lifecycle, from PubMed paper to user-visible table row:

```mermaid
graph TD
    pubmed["PubMed API"] --> step1["run-pipeline<br/>(Step 1)"]
    apis["NCBI / UniProt /<br/>Unpaywall APIs"] --> step2["sync-external<br/>(Step 2)"]

    step1 --> pg[("PostgreSQL<br/>[genes] / <br/>[trials] / <br/>[pubmed_refs]")]
    step2 --> pg_cache[("PostgreSQL<br/>[ncbi_gene_info] / <br/>[uniprot_info] / <br/>[pubmed_citations]")]

    pg --> step3["generate-qs<br/>(Step 3)"]
    pg_cache --> step3

    step3 --> qspvc[("QS Data PVC<br/>1Gi")]
    qspvc --> step4["restart-dashboard<br/>(Step 4)"]
    step4 --> dashboard["Dashboard<br/>reads QS files"]
```

Key points:

- The dashboard never queries PostgreSQL at runtime. It reads pre-built QS files.
- The QS Data PVC is the bridge between the pipeline (writes) and the dashboard (reads).
- The pipeline restarts the dashboard after writing new QS files so the fresh data is loaded.

---

## Shared Storage

The chart provisions five persistent volumes. All use `ReadWriteOnce` by default.

| PVC | Size | Owner | Purpose |
| ----- | ------ | ------- | --------- |
| `<release>-qs-data` | 1Gi | Pipeline (write), Dashboard (read) | Serialized QS data files |
| `<release>-postgresql-data-0` | 10Gi | PostgreSQL | Database files |
| `<release>-ntfy-cache` | 1Gi | ntfy | Message cache |
| `<release>-healthchecks-data` | 1Gi | Healthchecks | SQLite database |
| VictoriaLogs (subchart) | 10Gi | VictoriaLogs | Log storage |

**Scaling note**: The QS Data PVC defaults to `ReadWriteOnce`, which means only pods on the
same node can mount it simultaneously. This works when the dashboard has one replica. To
run multiple dashboard replicas across nodes, change `qsData.storage.accessMode` to
`ReadWriteMany` and use a CSI driver that supports it (NFS, EFS, CephFS).

---

## Networking

### Internal Services

Every component gets a ClusterIP Service for in-cluster communication:

| Service | Port | Type | Notes |
| --------- | ------ | ------ | ------- |
| Dashboard | 3838 | ClusterIP | Standard |
| PostgreSQL | 5432 | ClusterIP (headless) | `clusterIP: None` for StatefulSet |
| ntfy | 80 | ClusterIP | Conditional |
| Healthchecks | 8000 | ClusterIP | Conditional |
| Grafana Image Renderer | 8081 | ClusterIP | Conditional |
| Blackbox Exporter | 9115 | ClusterIP | Conditional |

### Ingress

**Template**: `templates/ingress.yaml`
**Condition**: `ingress.enabled` (default: `true`)

The Ingress resource maps external hostnames to internal services. It requires an
nginx-ingress-controller already running in the cluster.

| Hostname key | Backend Service | Port | Condition |
| -------------- | ---------------- | ------ | ----------- |
| `hosts.dashboard` | Dashboard | 3838 | Always |
| `hosts.ntfy` | ntfy | 80 | `notifications.ntfy.enabled` |
| `hosts.healthchecks` | Healthchecks | 8000 | `notifications.healthchecks.enabled` |
| `hosts.grafana` | Grafana (subchart or ExternalName) | 80 | `observability.prometheus.enabled` OR `observability.grafana.external.enabled` |
| `hosts.prometheus` | `<release>-kube-prometheus-stack-prometheus` | 9090 | `observability.prometheus.enabled` |
| `hosts.alertmanager` | `<release>-kube-prometheus-stack-alertmanager` | 9093 | `observability.prometheus.enabled` |
| `hosts.victoriaLogs` | `<release>-victoria-logs-single-server` | 9428 | `observability.victoriaLogs.enabled` |

The Grafana route has precedence: when `observability.prometheus.enabled` is `true` the
Ingress routes to the subchart's Grafana; otherwise, if
`observability.grafana.external.enabled` is `true`, it routes to the ExternalName
Service defined in `templates/grafana-external-service.yaml`.

**TLS**: Defaults to `enabled: true` with `secretName: cloudflare-origin-tls`. The
`tls:` block automatically includes every hostname whose component is enabled. Provide
either a pre-existing secret (`ingress.tls.secretName`) or a cert-manager cluster
issuer (`ingress.tls.clusterIssuer`) for automatic certificate provisioning.

### Network Policies (optional)

**Template**: `templates/network-policies.yaml`
**Condition**: `networkPolicies.enabled` (default: `false`)

When enabled (requires a CNI that supports NetworkPolicy, such as Calico or Cilium),
seven policies restrict pod-to-pod traffic. Both **ingress** and **egress** are
constrained — egress is **not** unrestricted.

**Ingress policies** (who may connect into each component):

| Target | Allowed Sources | Port |
| -------- | ---------------- | ------ |
| PostgreSQL | Pipeline pods | 5432 |
| PostgreSQL | Prometheus pods from `monitoring` namespace (when exporter enabled) | 9187 |
| Dashboard | `ingress-nginx` namespace | 3838 |
| ntfy | `ingress-nginx` namespace + Pipeline pods | 80 |
| ntfy | Prometheus pods from `monitoring` namespace (when metrics enabled) | 80 |
| Healthchecks | `ingress-nginx` namespace + Pipeline pods | 8000 |
| Healthchecks | Blackbox Exporter pods (when blackbox enabled) | 8000 |

**Egress policies** (where each component may connect out):

| Component | Allowed Destinations |
| ----------- | ---------------------- |
| Pipeline | DNS (UDP/TCP 53), PostgreSQL (5432), and HTTPS (443) for PubMed, NCBI, UniProt, Anthropic, Unpaywall |
| Dashboard | DNS (53) and HTTPS (443) for ClinicalTrials.gov + geocoding |
| PostgreSQL | DNS (53) only |

The remaining components (ntfy, Healthchecks, Blackbox, Image Renderer) have no egress
NetworkPolicy — they inherit the cluster default (allow all egress).

---

## Security

### Non-root Containers

Most workloads run as non-root users with privilege escalation disabled:

| Component | UID | GID | fsGroup | Capabilities | Extras |
| ----------- | ----- | ----- | --------- | ------------- | -------- |
| Dashboard | 997 | 997 | 997 | Drop ALL | -- |
| PostgreSQL | 999 | 999 | 999 | Drop ALL | -- |
| Pipeline | 65534 | 65534 | -- | Drop ALL | `seccompProfile: RuntimeDefault` |
| Grafana Image Renderer | 472 | -- | -- | Drop ALL | -- |

The dashboard has an init container that runs as root (uid 0) solely to fix file ownership
on the QS data volume. This is a common Kubernetes pattern for shared PVCs where the writer
(pipeline, uid 65534) and reader (dashboard, uid 997) run as different users.

### Scoped Secrets

Secrets are split by concern so that each component only sees the credentials it needs:

| Secret | Contents | Mounted By |
| -------- | ---------- | ------------ |
| `<release>-svd-dashboard-db-credentials` | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | PostgreSQL, Pipeline (all steps), postgres_exporter |
| `<release>-svd-dashboard-pipeline-secrets` | `ANTHROPIC_API_KEY`, `NCBI_API_KEY`, `ENTREZ_EMAIL`, `UNPAYWALL_EMAIL`, `PIPELINE_NOTIFY_URLS`, `PIPELINE_HEALTHCHECK_URL` | Pipeline Steps 1 & 2 (full envFrom); Step 3 only reads `NCBI_API_KEY` + `ENTREZ_EMAIL` |
| `<release>-svd-dashboard-renviron` | `.Renviron` file (DB credentials + `NCBI_API_KEY` + `ENTREZ_EMAIL`) | Dashboard only (as a file subPath) |
| `<release>-svd-dashboard-healthchecks` | Django `SECRET_KEY` | Healthchecks only |

The dashboard never sees the `ANTHROPIC_API_KEY`. The pipeline never sees the Healthchecks
`SECRET_KEY`. Each component operates on a need-to-know basis.

### RBAC

A dedicated ServiceAccount, Role, and RoleBinding are created for the pipeline CronJob. The
Role grants only two verbs (`get`, `patch`) on one resource type (`deployments`) -- the
minimum permission needed to trigger `kubectl rollout restart`.

No other component requires custom RBAC. The dashboard and notification services use the
default ServiceAccount.

### PodDisruptionBudgets

**Template**: `templates/pdb.yaml`

Two PDBs ensure availability during voluntary disruptions (node drains, cluster upgrades):

| Component | Policy |
| ----------- | -------- |
| Dashboard | `minAvailable: 1` |
| PostgreSQL | `minAvailable: 1` |

These prevent Kubernetes from evicting the last running pod of either component during
planned maintenance.

---

## Configuration

Everything is driven by `values.yaml`. Here are the most important knobs:

### Essentials (must be set)

```yaml
postgresql:
  credentials:
    username: "your-db-user"
    password: "your-db-password"

secrets:
  anthropicApiKey: "sk-ant-..."
  ncbiApiKey: "..."
  entrezEmail: "you@example.com"
  unpaywallEmail: "you@example.com"
```

### Pipeline Schedule

```yaml
pipeline:
  schedule: "0 3 * * 1"        # Cron expression (default: 3 AM Monday)
  daysBack: 7                  # How many days of PubMed to search
  syncExternalData: true       # Whether to sync NCBI/UniProt/PubMed caches
  activeDeadlineSeconds: 7200  # Max runtime before the job is killed
```

### Ingress Hostnames

```yaml
ingress:
  hosts:
    dashboard: shiny.example.com
    grafana: grafana.example.com
    ntfy: ntfy.example.com
    healthchecks: healthchecks.example.com
    prometheus: prometheus.example.com       # only routed if prometheus.enabled
    alertmanager: alertmanager.example.com   # only routed if prometheus.enabled
    victoriaLogs: victoria-logs.example.com  # only routed if victoriaLogs.enabled
  tls:
    enabled: true
    secretName: cloudflare-origin-tls        # or clusterIssuer: letsencrypt-prod
```

### Monitoring

```yaml
monitoring:
  serviceMonitor:
    enabled: true
    labels:
      release: prometheus       # Must match Prometheus' serviceMonitorSelector
    interval: 30s
  ingressNginx:
    enabled: true
    namespace: ingress-nginx    # Namespace where ingress-nginx runs
    portName: metrics
    metricsPort: 10254
    selectorLabels:
      app.kubernetes.io/name: ingress-nginx
      app.kubernetes.io/component: controller
  blackboxExporter:
    enabled: true
```

### PostgreSQL Exporter

```yaml
postgresql:
  exporter:
    enabled: true               # Deploy postgres_exporter sidecar
    image:
      repository: prometheuscommunity/postgres-exporter
      tag: "v0.16.0"
    port: 9187
```

**Defaults note**: The chart ships with an internal observability posture —
`observability.prometheus.enabled` and `observability.victoriaLogs.enabled` both
default to `true`, and `observability.grafana.external.enabled` defaults to `false`.
If you already run kube-prometheus-stack elsewhere in the cluster, disable the subchart
and flip `observability.grafana.external.enabled` to `true` to route the Grafana
Ingress to the shared instance instead.

### Resource Tuning

| Component | Default CPU (req/lim) | Default Memory (req/lim) |
| ----------- | ---------------------- | ------------------------- |
| Dashboard | 1000m / 4000m | 2Gi / 4Gi |
| PostgreSQL | 500m / 750m | 512Mi / 768Mi |
| Pipeline | 750m / 1000m | 1Gi / 2Gi |
| ntfy | 500m / 750m | 512Mi / 768Mi |
| Healthchecks | 500m / 750m | 512Mi / 768Mi |
| Image Renderer | 250m / 1000m | 512Mi / 1Gi |
| Blackbox Exporter | 250m / 500m | 128Mi / 256Mi |
| PostgreSQL Exporter | 250m / 500m | 256Mi / 512Mi |

### Storage Sizing

```yaml
postgresql:
  storage:
    size: 10Gi
qsData:
  storage:
    size: 1Gi
    accessMode: ReadWriteOnce  # Change to ReadWriteMany for multi-replica dashboard
victoria-logs-single:
  server:
    persistentVolume:
      size: 10Gi                # VictoriaLogs log storage (when enabled)
```

### VictoriaLogs Datasource

```yaml
observability:
  victoriaLogs:
    enabled: true
    datasource:
      uid: "ffffwg4f57a4gd"     # Stable UID referenced by dashboards
      url: "http://svd-victoria-logs-single-server.svd.svc.cluster.local:9428"
  grafanaImageRenderer:
    enabled: true
```

---

## Feature Flags

Components can be toggled on or off without removing template files:

| Flag | Default | What It Controls |
| ------ | --------- | ----------------- |
| `notifications.ntfy.enabled` | `true` | ntfy Deployment, Service, PVC, Ingress rule, NetworkPolicy |
| `notifications.ntfy.metrics.enabled` | `true` | ntfy ServiceMonitor + `NTFY_ENABLE_METRICS` env var |
| `notifications.healthchecks.enabled` | `true` | Healthchecks Deployment, Service, PVC, Secret, Ingress rule, NetworkPolicy |
| `observability.prometheus.enabled` | `true` | kube-prometheus-stack subchart + Prometheus/Alertmanager/Grafana Ingress rules |
| `observability.victoriaLogs.enabled` | `true` | victoria-logs-single subchart, VictoriaLogs datasource ConfigMap, VictoriaLogs Ingress rule |
| `observability.grafanaImageRenderer.enabled` | `true` | Grafana Image Renderer Deployment + Service |
| `observability.grafana.external.enabled` | `false` | ExternalName Service for Grafana in another namespace (only rendered when `prometheus.enabled` is `false`) |
| `postgresql.exporter.enabled` | `true` | postgres_exporter sidecar + metrics port on PostgreSQL Service |
| `monitoring.serviceMonitor.enabled` | `true` | All ServiceMonitor and Probe resources |
| `monitoring.blackboxExporter.enabled` | `true` | Blackbox Exporter Deployment, ConfigMap, Service |
| `monitoring.ingressNginx.enabled` | `true` | Ingress-Nginx ServiceMonitor + Metrics Service |
| `networkPolicies.enabled` | `false` | All NetworkPolicy resources (requires CNI support) |
| `ingress.enabled` | `true` | Ingress resource |
| `ingress.tls.enabled` | `true` | TLS termination and cert-manager annotation |
| `pipeline.syncExternalData` | `true` | sync-external init container in CronJob |

---

## Glossary

| Term | Meaning |
| ------ | --------- |
| **Blackbox Exporter** | A Prometheus exporter that probes endpoints over HTTP, HTTPS, DNS, TCP, ICMP and reports whether they are reachable. Used here to monitor dashboard and Healthchecks uptime. |
| **ClusterIP** | The default Service type. Gives a pod an internal IP address reachable only from inside the cluster -- like an office extension number that does not work from outside the building. |
| **CNI** | Container Network Interface. The plugin that handles networking between pods. Some CNIs (Calico, Cilium) support NetworkPolicy; simpler ones (Flannel) do not. |
| **ConfigMap** | A Kubernetes object that holds non-secret configuration data (files, environment variables). This chart uses one to ship SQL schema files into the PostgreSQL container. |
| **CronJob** | A Kubernetes resource that creates a Job on a repeating schedule, like a cron entry on a Linux server. The pipeline runs as a CronJob. |
| **CSI** | Container Storage Interface. A standard that lets Kubernetes use external storage systems (NFS, AWS EBS, Ceph) as persistent volumes. |
| **Deployment** | A Kubernetes resource that manages a set of identical pods. If a pod crashes, the Deployment replaces it. The dashboard, ntfy, and Healthchecks all run as Deployments. |
| **envFrom** | A pod spec field that loads every key in a Secret or ConfigMap as an environment variable at once, rather than mapping them one by one. |
| **ExternalName Service** | A Service that acts as a DNS alias for something outside the current namespace. Used here to point to a Grafana instance in a separate namespace. |
| **Headless Service** | A Service with `clusterIP: None`. Instead of a single virtual IP, it returns the individual pod IPs through DNS. Required for StatefulSets so each pod has a stable hostname. |
| **Helm** | A package manager for Kubernetes. A Helm chart is a bundle of templates and default values that produce the YAML manifests Kubernetes needs. `helm install` and `helm upgrade` apply them. |
| **Ingress** | A Kubernetes resource that maps external hostnames and paths to internal Services. It requires an Ingress controller (like nginx) to actually handle the traffic. Think of it as the front-desk directory in a building lobby. |
| **Init Container** | A container that runs to completion before the main container starts. If it fails, the pod restarts. The pipeline uses init containers to guarantee steps run in order. |
| **Job** | A Kubernetes resource that runs a container once (or a fixed number of times) and then stops. CronJobs create Jobs on a schedule. |
| **Namespace** | A virtual partition inside a Kubernetes cluster. Resources in one namespace are isolated from those in another by default. This chart assumes all resources live in the same namespace. |
| **NetworkPolicy** | A firewall rule for pods. It defines which pods can talk to which other pods on which ports. Requires a CNI that supports it. |
| **PersistentVolumeClaim (PVC)** | A request for storage. When a pod mounts a PVC, Kubernetes provisions a disk (or reuses an existing one) and attaches it. Data on a PVC survives pod restarts. |
| **postgres_exporter** | A Prometheus exporter sidecar that connects to PostgreSQL and exposes database metrics (connections, queries, replication lag, etc.) on a `/metrics` HTTP endpoint. |
| **Pod** | The smallest deployable unit in Kubernetes -- one or more containers that share networking and storage. Every workload ultimately runs inside a pod. |
| **Probe** | A custom resource from the Prometheus Operator. Defines blackbox-style endpoint checks that Prometheus executes via the Blackbox Exporter. |
| **PodDisruptionBudget (PDB)** | A policy that tells Kubernetes how many pods of a given type must stay running during voluntary disruptions like node drains. `minAvailable: 1` means "never evict the last one." |
| **QS** | A fast binary serialization format for R objects (from the `qs` package). About 3-5x faster than the standard RDS format. The pipeline writes QS files; the dashboard reads them. |
| **RBAC** | Role-Based Access Control. Kubernetes permissions system. A Role defines allowed actions; a RoleBinding grants that Role to a ServiceAccount or user. |
| **ReadWriteOnce (RWO)** | A PVC access mode where only pods on a single node can mount the volume. Fine for single-replica workloads. For multi-node access, use ReadWriteMany (RWX). |
| **Rollout Restart** | A `kubectl` command that triggers a zero-downtime restart of a Deployment by creating new pods before terminating old ones. The pipeline uses this to reload the dashboard after updating QS files. |
| **Secret** | Like a ConfigMap, but for sensitive data (passwords, API keys). Values are base64-encoded at rest and can be mounted as files or environment variables. |
| **ServiceAccount** | An identity for pods. When a pod needs to call the Kubernetes API (like restarting a Deployment), it authenticates using its ServiceAccount and the RBAC rules bound to it. |
| **ServiceMonitor** | A custom resource from the Prometheus Operator. It tells Prometheus which Services to scrape for metrics, on which port, and at what interval. |
| **StatefulSet** | Like a Deployment, but for workloads that need stable identities and persistent storage -- typically databases. Each pod gets a fixed name (`postgresql-0`) and its own PVC. |
| **Subchart** | A Helm chart listed as a dependency of another chart. This chart uses two subcharts: kube-prometheus-stack (Prometheus + Grafana) and victoria-logs-single (VictoriaLogs + Vector). |
| **TLS** | Transport Layer Security. Encrypts traffic between the user's browser and the Ingress controller. Certificates can be provided manually or auto-provisioned by cert-manager. |
| **values.yaml** | The configuration file for a Helm chart. Every setting (image tags, resource limits, feature flags, secrets) is defined here and injected into templates at render time. |
| **Vector** | A log collection agent (by Datadog). Deployed alongside VictoriaLogs to collect stdout/stderr from all pods and forward them for storage and querying. |
