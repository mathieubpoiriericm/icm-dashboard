# Kubernetes Namespace Architecture

This document describes the namespaces in the SVD Dashboard Kubernetes cluster (Docker Desktop) and the pods within each.

## Cluster Overview

| Namespace | Purpose | Pods |
|-----------|---------|------|
| `kube-system` | Kubernetes control plane and core services | 10 |
| `ingress-nginx` | HTTP ingress controller | 1 |
| `monitoring` | Observability stack (Prometheus, Grafana, VictoriaLogs) | 9 |
| `svd` | Application stack (Shiny dashboard, PostgreSQL, notifications) | 5 pods + 1 CronJob |
| `default` | Unused (Kubernetes default) | 0 |
| `kube-node-lease` | Node heartbeat leases | 0 |
| `kube-public` | Unused (Kubernetes default) | 0 |

## `kube-system` — Kubernetes Core Infrastructure

The control plane and cluster services that make Kubernetes itself work. All managed by Docker Desktop.

| Pod | Purpose |
|-----|---------|
| `kube-apiserver-docker-desktop` | API server — all `kubectl` commands go through here |
| `etcd-docker-desktop` | Key-value store holding all cluster state |
| `kube-controller-manager-docker-desktop` | Runs controllers (Deployments, ReplicaSets, etc.) |
| `kube-scheduler-docker-desktop` | Assigns pods to nodes |
| `kube-proxy` | Network rules for Service routing |
| `coredns` (x2) | Cluster DNS — resolves service names (e.g. `prometheus-grafana.monitoring.svc.cluster.local`) |
| `storage-provisioner` | Docker Desktop's local PersistentVolume provisioner |
| `metrics-server` | Kubernetes Metrics Server — enables `kubectl top` and HPA autoscaling |
| `vpnkit-controller` | Docker Desktop networking bridge between macOS and the Linux VM |

## `ingress-nginx` — Ingress Controller

The single entry point for all external HTTP traffic into the cluster.

| Pod | Purpose |
|-----|---------|
| `ingress-nginx-controller` | Nginx reverse proxy routing `*.local` hostnames to backend services |

### Routing Rules

All routing is defined in the `svd-svd-dashboard` Ingress resource in the `svd` namespace:

| Hostname | Backend Service | Namespace | Port |
|----------|----------------|-----------|------|
| `shiny.local` | `svd-svd-dashboard-dashboard` | `svd` | 3838 |
| `grafana.local` | `svd-svd-dashboard-grafana-external` (ExternalName → `prometheus-grafana.monitoring`) | `svd` → `monitoring` | 80 |
| `ntfy.local` | `svd-svd-dashboard-ntfy` | `svd` | 80 |
| `healthchecks.local` | `svd-svd-dashboard-healthchecks` | `svd` | 8000 |

## `monitoring` — Observability Stack

Deployed via the `kube-prometheus-stack` Helm chart (release name: `prometheus`) and a separate `victoria-logs-single` Helm chart. Provides centralized metrics collection, alerting, dashboarding, and log aggregation.

### Metrics Pipeline

| Pod | Purpose |
|-----|---------|
| `prometheus-prometheus-kube-prometheus-prometheus-0` | Prometheus server — scrapes and stores time-series metrics (2 containers: `prometheus` + `config-reloader` sidecar, plus `init-config-reloader` init container) |
| `prometheus-kube-prometheus-operator` | Manages Prometheus CRDs (`ServiceMonitor`, `PrometheusRule`, `Alertmanager`) |
| `prometheus-kube-state-metrics` | Exports Kubernetes object metrics (pod status, deployment replicas, etc.) |
| `prometheus-prometheus-node-exporter` | Exports host OS metrics (CPU, memory, disk, network) |
| `alertmanager-prometheus-kube-prometheus-alertmanager-0` | Routes and manages alerts from Prometheus (2 containers: `alertmanager` + `config-reloader` sidecar, plus `init-config-reloader` init container) |

### Dashboarding

| Pod | Purpose |
|-----|---------|
| `prometheus-grafana` | Grafana dashboard server (3 containers: grafana + 2 sidecars for dashboard/datasource provisioning) |
| `grafana-image-renderer` | Go-based Chromium renderer for Grafana image/PDF export |

### Log Aggregation

| Pod | Purpose |
|-----|---------|
| `victoria-logs-victoria-logs-single-server-0` | VictoriaLogs — log storage backend |
| `victoria-logs-vector` | Vector log collector — ships container and node logs to VictoriaLogs |

## `svd` — Application Stack

Deployed via the `svd-dashboard` Helm chart (release name: `svd`). Contains the R Shiny dashboard and all supporting services.

### Core Application

| Pod | Purpose |
|-----|---------|
| `svd-svd-dashboard-dashboard` | The R Shiny dashboard — serves the web UI at `shiny.local`, reads QS data files at runtime. Includes `fix-qs-permissions` init container that fixes PVC ownership before the dashboard starts. |
| `svd-svd-dashboard-postgresql-0` | PostgreSQL 18 database storing extracted gene data (StatefulSet with persistent storage). Runs 2 containers: `postgresql` + `postgres-exporter` sidecar (Prometheus metrics on port 9187). |

### Notification Services

| Pod | Purpose |
|-----|---------|
| `svd-svd-dashboard-ntfy` | ntfy push notification server — receives pipeline alerts |
| `svd-svd-dashboard-healthchecks` | Healthchecks cron monitoring — tracks pipeline CronJob execution |

### Monitoring Support

| Pod | Purpose |
|-----|---------|
| `svd-svd-dashboard-blackbox-exporter` | Probes HTTP endpoints (`shiny.local`, `ntfy.local`, `healthchecks.local`) and exposes availability metrics to Prometheus |

### Pipeline CronJob

The `svd-svd-dashboard-pipeline` CronJob runs the weekly ETL pipeline that extracts gene data from PubMed, syncs external data, regenerates QS files, and restarts the dashboard.

| Property | Value |
|----------|-------|
| **Schedule** | `0 3 * * 1` (every Monday at 3:00 AM) |
| **ServiceAccount** | `svd-svd-dashboard-pipeline` (Role grants `get`/`patch` on deployments) |
| **Shared storage** | Mounts the `qs-data` PVC (same volume used by the dashboard) |

**Execution sequence** — 3 init containers run sequentially, then the main container:

| Step | Container | Command | Purpose |
|------|-----------|---------|---------|
| 1 | `run-pipeline` (init) | `python pipeline/main.py --days-back 7` | Search PubMed for new papers, extract genes via LLM, load into PostgreSQL |
| 2 | `sync-external` (init) | `python pipeline/main.py --sync-external-data` | Sync NCBI Gene, UniProt, and PubMed citation data |
| 3 | `generate-qs` (init) | `Rscript scripts/trigger_update.R` | Read from PostgreSQL and regenerate QS data files on the shared PVC |
| 4 | `restart-dashboard` (main) | `kubectl rollout restart deployment/svd-svd-dashboard-dashboard` | Rolling restart of the Shiny dashboard to pick up new QS files |

### Cross-Namespace Services

The `svd` namespace contains an **ExternalName** service (`svd-svd-dashboard-grafana-external`) that aliases `prometheus-grafana.monitoring.svc.cluster.local`. This allows the `grafana.local` ingress rule to route traffic to the Grafana pod in the `monitoring` namespace without duplicating the Grafana deployment.

### PodDisruptionBudgets

Two PDBs ensure availability during voluntary disruptions (node drains, upgrades):

| PDB | Target | Policy |
|-----|--------|--------|
| `svd-svd-dashboard-dashboard` | Dashboard Deployment | `minAvailable: 1` |
| `svd-svd-dashboard-postgresql` | PostgreSQL StatefulSet | `minAvailable: 1` |

## Data Flow Between Namespaces

```mermaid
flowchart TD
    Browser["Browser (*.local)"]

    Browser -->|"Host header routing"| Ingress

    subgraph ingress-nginx
        Ingress["ingress-nginx-controller"]
    end

    Ingress -->|"shiny.local"| Shiny
    Ingress -->|"grafana.local (via ExternalName)"| Grafana
    Ingress -->|"ntfy.local"| Ntfy
    Ingress -->|"healthchecks.local"| Healthchecks

    subgraph svd namespace
        Shiny["Shiny App"]
        QS["QS Files (PVC)"]
        PG["PostgreSQL"]
        Ntfy["ntfy"]
        Healthchecks["Healthchecks"]
        Blackbox["Blackbox Exporter"]
        Pipeline["Pipeline CronJob\n(weekly Mon 3 AM)"]

        Shiny --> QS
        Pipeline -->|"1. extract genes"| PG
        Pipeline -->|"2. sync external data"| PG
        Pipeline -->|"3. generate QS"| QS
        Pipeline -->|"4. rollout restart"| Shiny
        Pipeline -->|"alerts"| Ntfy
        Pipeline -->|"heartbeat"| Healthchecks
    end

    subgraph monitoring namespace
        Grafana["Grafana"]
        Renderer["Image Renderer"]
        Prometheus["Prometheus"]
        VictoriaLogs["VictoriaLogs"]
        Vector["Vector"]

        Grafana -->|"render requests"| Renderer
        Prometheus -->|"queries"| Grafana
        Vector -->|"ships logs"| VictoriaLogs
    end

    Prometheus -->|"scrapes metrics"| Blackbox
    Prometheus -->|"scrapes metrics"| PG
    Prometheus -->|"scrapes metrics"| Ntfy
    Blackbox -->|"probes endpoints"| Shiny
    Blackbox -->|"probes endpoints"| Ntfy
    Blackbox -->|"probes endpoints"| Healthchecks

    %% Node colors by namespace
    classDef ingress fill:#4a90d9,stroke:#3a7bc8,color:#fff
    classDef svdNode fill:#50b878,stroke:#40a868,color:#fff
    classDef monNode fill:#e8913a,stroke:#d8812a,color:#fff
    classDef external fill:#888,stroke:#777,color:#fff

    class Browser external
    class Ingress ingress
    class Shiny,QS,PG,Ntfy,Healthchecks,Blackbox,Pipeline svdNode
    class Grafana,Renderer,Prometheus,VictoriaLogs,Vector monNode

    %% Edge colors by flow type
    linkStyle 0,1,2,3,4 stroke:#4a90d9,stroke-width:2px
    linkStyle 5,6,7,8,9,10,11 stroke:#50b878,stroke-width:2px
    linkStyle 12,13,14,15,16,17,18,19,20 stroke:#e8913a,stroke-width:2px
```

| Color | Flow Type | Description |
|-------|-----------|-------------|
| Blue | Ingress routing | Browser to nginx to backend services |
| Green | Pipeline & data | CronJob to PostgreSQL to QS files to Shiny |
| Orange | Monitoring | Prometheus scrapes, Blackbox probes, log shipping |
