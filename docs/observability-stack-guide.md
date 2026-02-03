# Observability Stack Guide for the Cerebral SVD Dashboard

This guide walks you through implementing a complete observability stack for monitoring the Cerebral Small Vessel Disease (SVD) Dashboard running on self-hosted Kubernetes.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Introduction](#introduction)
  - [What You Will Build](#what-you-will-build)
  - [Architecture Overview](#architecture-overview-1)
  - [Prerequisites](#prerequisites)
- [Preparation](#preparation)
  - [Create the Monitoring Namespace](#create-the-monitoring-namespace)
  - [Add Helm Repositories](#add-helm-repositories)
- [Install kube-prometheus-stack (Metrics)](#install-kube-prometheus-stack-metrics)
  - [What Gets Installed](#what-gets-installed)
  - [Install the Chart](#install-the-chart)
  - [Access Grafana](#access-grafana)
- [Install VictoriaLogs (Logs)](#install-victorialogs-logs)
  - [Create Values File](#create-values-file)
  - [Install VictoriaLogs](#install-victorialogs)
- [Querying Logs with LogsQL](#querying-logs-with-logsql)
  - [Basic Query Syntax](#basic-query-syntax)
  - [Count Unique SVD Dashboard Visitors](#count-unique-svd-dashboard-visitors)
  - [SVD Dashboard Visitors Over Time](#svd-dashboard-visitors-over-time)
  - [Top IPs Accessing the SVD Dashboard](#top-ips-accessing-the-svd-dashboard)
- [Create the SVD Dashboard Monitoring Dashboard](#create-the-svd-dashboard-monitoring-dashboard)
  - [Panel 1: Unique Visitors Today](#panel-1-unique-visitors-today-stat)
  - [Panel 2: SVD Dashboard CPU Usage](#panel-2-svd-dashboard-cpu-usage-time-series)
  - [Panel 3: SVD Dashboard Memory Usage](#panel-3-svd-dashboard-memory-usage-time-series)
- [Troubleshooting](#troubleshooting)
- [Maintenance and Operations](#maintenance-and-operations)
  - [Upgrading the Stack](#upgrading-the-stack)
  - [Scaling for Production](#scaling-for-production)
- [Kubernetes Configuration Files](#kubernetes-configuration-files)
  - [NGINX Ingress ConfigMap](#nginx-ingress-configmap)
  - [RShiny Application Deployment](#rshiny-application-deployment)
  - [RShiny Ingress](#rshiny-ingress)
  - [Grafana Image Renderer](#grafana-image-renderer)
- [Grafana Dashboard Configuration](#grafana-dashboard-configuration)
- [Appendix: Glossary](#appendix-glossary)
- [Appendix: Quick Reference Card](#appendix-quick-reference-card)

---

## Architecture Overview

The following diagram shows how data flows through the observability stack for monitoring the Cerebral SVD Dashboard application:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA SOURCES                                   │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐              │
│  │ Cerebral SVD    │  │ nginx-ingress   │  │ Kubernetes      │              │
│  │ Dashboard       │  │ Controller      │  │ API Server      │              │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘              │
│           │ metrics            │ logs               │ state                 │
└───────────┼────────────────────┼────────────────────┼───────────────────────┘
            ↓                    ↓                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                              COLLECTORS                                     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐              │
│  │ cAdvisor        │  │ Vector          │  │ kube-state-     │              │
│  │ (kubelet)       │  │ (DaemonSet)     │  │ metrics         │              │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘              │
└───────────┼────────────────────┼────────────────────┼───────────────────────┘
            ↓                    ↓                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                               STORAGE                                       │
│       ┌─────────────────┐           ┌─────────────────┐                     │
│       │   Prometheus    │           │   VictoriaLogs  │                     │
│       └────────┬────────┘           └────────┬────────┘                     │
│                │ PromQL                      │ LogsQL                       │
└────────────────┼─────────────────────────────┼──────────────────────────────┘
                 └──────────────┬──────────────┘
                                ↓
                    ┌─────────────────────┐
                    │       Grafana       │
                    │     Dashboards      │
                    └─────────────────────┘
```

| Layer | Component | Function |
|-------|-----------|----------|
| Data Sources | SVD Dashboard, nginx-ingress, K8s API | Generate metrics and logs |
| Collectors | cAdvisor, Vector, kube-state-metrics | Gather and forward data |
| Storage | Prometheus, VictoriaLogs | Time-series and log databases |
| Visualization | Grafana | Unified dashboards and queries |

> [!NOTE]
> **Key Data Flows:**
> - **Metrics path:** Containers → cAdvisor → Prometheus → Grafana (PromQL)
> - **Logs path:** Container stdout → Vector → VictoriaLogs → Grafana (LogsQL)
> - **Unique IPs:** nginx-ingress logs → Vector parses `client_ip` → VictoriaLogs `count_uniq()`

---

## Introduction

### What You Will Build

This guide walks you through implementing a complete observability stack for monitoring the Cerebral Small Vessel Disease (SVD) Dashboard running on self-hosted Kubernetes. The SVD Dashboard is an R Shiny application that provides researchers with interactive tools for exploring cerebral small vessel disease genetics data. By the end of this guide, you will have:

- **Container metrics monitoring** — CPU, memory, and network usage for the SVD Dashboard pods
- **Log aggregation** — Centralized logs from all dashboard containers
- **Unique visitor tracking** — Count distinct IP addresses accessing the SVD Dashboard
- **Unified dashboards** — Single Grafana interface for monitoring dashboard metrics and logs

### Architecture Overview

The observability stack for the Cerebral SVD Dashboard consists of the following components:

| Component | Purpose | Data Type |
|-----------|---------|-----------|
| Prometheus | Time-series database | Metrics |
| Grafana | Visualization | Dashboards |
| VictoriaLogs | Log database | Logs |
| Vector | Log collector | Logs |
| Alertmanager | Alert routing | Notifications |

### Prerequisites

This guide is written for deploying the Cerebral SVD Dashboard observability stack on **macOS using Docker Desktop** with Kubernetes enabled.

> [!WARNING]
> **Docker Desktop Limitations:** Node-exporter cannot run on Docker Desktop due to mount propagation restrictions. This guide disables it by default. You will still have full container metrics via cAdvisor.

Before starting, ensure you have:

1. **macOS** with Docker Desktop installed
2. **Kubernetes enabled** in Docker Desktop settings
3. `kubectl` installed (`brew install kubectl`)
4. `helm` v3.0+ installed (`brew install helm`)
5. Docker Desktop allocated at least **4 CPU cores** and **8GB RAM**

---

## Preparation

### Create the Monitoring Namespace

All monitoring components will be deployed to a dedicated namespace called `monitoring`.

```bash
kubectl create namespace monitoring
```

### Add Helm Repositories

Add the required Helm chart repositories:

```bash
# Add Prometheus community charts
helm repo add prometheus-community \
    https://prometheus-community.github.io/helm-charts

# Add VictoriaMetrics charts
helm repo add vm \
    https://victoriametrics.github.io/helm-charts/

# Update repository cache
helm repo update
```

> [!TIP]
> You have completed the preparation phase. Your cluster is ready for the SVD Dashboard monitoring stack installation.

---

## Install kube-prometheus-stack (Metrics)

The `kube-prometheus-stack` Helm chart deploys Prometheus, Grafana, Alertmanager, and various exporters in a single installation.

### What Gets Installed

| Component | Role |
|-----------|------|
| Prometheus | Collects and stores metrics |
| Grafana | Visualization dashboards |
| Alertmanager | Alert routing and notifications |
| kube-state-metrics | Kubernetes object state metrics |
| node-exporter | Host-level metrics (disabled on Docker Desktop) |
| Prometheus Operator | Manages Prometheus lifecycle via CRDs |

### Install the Chart

```bash
helm install prometheus prometheus-community/kube-prometheus-stack \
    --namespace monitoring \
    --set prometheus.prometheusSpec.retention=15d \
    --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=50Gi \
    --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
    --set nodeExporter.enabled=false
```

> [!NOTE]
> **Explanation of flags:**
> - `retention=15d` — Keep metrics for 15 days
> - `storage=50Gi` — Allocate 50GB for metrics storage
> - `serviceMonitorSelectorNilUsesHelmValues=false` — Discover all ServiceMonitors
> - `nodeExporter.enabled=false` — Required for Docker Desktop

### Access Grafana

#### Start Port Forward

```bash
kubectl port-forward svc/prometheus-grafana 3000:80 -n monitoring
```

#### Get Admin Password

```bash
kubectl get secret prometheus-grafana -n monitoring \
    -o jsonpath="{.data.admin-password}" | base64 --decode; echo
```

#### Login to Grafana

1. Open <http://localhost:3000> in your browser
2. Username: `admin`
3. Password: (output from the command above)

---

## Install VictoriaLogs (Logs)

VictoriaLogs provides log aggregation with native support for high-cardinality fields like IP addresses.

### Create Values File

Create a file named `victorialogs-values.yaml`:

<details>
<summary>Click to expand victorialogs-values.yaml</summary>

```yaml
server:
  retentionPeriod: 30d

  persistentVolume:
    enabled: true
    size: 50Gi
    storageClass: ""

  resources:
    requests:
      cpu: 200m
      memory: 256Mi
    limits:
      cpu: 1000m
      memory: 1Gi

vector:
  enabled: true

  customConfig:
    sources:
      kubernetes_logs:
        type: kubernetes_logs

    transforms:
      parse_nginx:
        type: remap
        inputs: ["kubernetes_logs"]
        source: |
          parsed, err = parse_json(.message)
          if err == null {
            .client_ip = parsed.remote_addr
            .request_uri = parsed.request_uri
            .status = parsed.status
          }

    sinks:
      vlogs:
        type: elasticsearch
        inputs: ["parse_nginx"]
        endpoints:
          - "http://victoria-logs-victoria-logs-single-server:9428/insert/elasticsearch"
        mode: bulk
        api_version: v8
        compression: gzip
```

</details>

### Install VictoriaLogs

```bash
helm install victoria-logs vm/victoria-logs-single \
    --namespace monitoring \
    -f victorialogs-values.yaml
```

> [!TIP]
> VictoriaLogs is now collecting logs from all containers in your cluster, including the Cerebral SVD Dashboard.

---

## Querying Logs with LogsQL

VictoriaLogs uses LogsQL, a query language optimized for log analysis. The following examples demonstrate common queries for monitoring the Cerebral SVD Dashboard.

### Basic Query Syntax

```
<time_filter> <field_filters> | <pipes>
```

- **Time filter:** `_time:1d` (last 1 day), `_time:1h` (last 1 hour)
- **Field filters:** `field_name:value` or `field_name:/regex/`
- **Pipes:** `stats`, `sort`, `limit`, `uniq`

### Count Unique SVD Dashboard Visitors

This query counts unique visitors to the Cerebral SVD Dashboard by counting distinct IP addresses:

```
_time:1d kubernetes.container_name:controller
  | stats count_uniq(client_ip) as unique_visitors
```

### SVD Dashboard Visitors Over Time

Track how many unique researchers are accessing the SVD Dashboard per hour:

```
_time:24h kubernetes.container_name:controller
  | stats by (_time:1h) count_uniq(client_ip) as unique_ips
```

### Top IPs Accessing the SVD Dashboard

Identify the most active users of the SVD Dashboard:

```
_time:1d kubernetes.container_name:controller
  | stats by (client_ip) count() as requests
  | sort by (requests) desc
  | limit 10
```

---

## Create the SVD Dashboard Monitoring Dashboard

This chapter walks you through creating a Grafana dashboard specifically for monitoring the Cerebral SVD Dashboard application.

### Panel 1: Unique Visitors Today (Stat)

| Setting | Value |
|---------|-------|
| Data source | VictoriaLogs |
| Query type | Instant |
| Visualization | Stat |

Query:

```
_time:1d kubernetes.container_name:controller
  | stats count_uniq(client_ip) as unique_visitors
```

### Panel 2: SVD Dashboard CPU Usage (Time Series)

Data source: Prometheus

Query (PromQL) — filters for SVD Dashboard pods:

```
sum(rate(container_cpu_usage_seconds_total{pod=~"rshiny.*"}[5m])) by (pod)
```

### Panel 3: SVD Dashboard Memory Usage (Time Series)

Data source: Prometheus

Query (PromQL) — filters for SVD Dashboard pods:

```
sum(container_memory_working_set_bytes{pod=~"rshiny.*"}) by (pod)
```

> [!TIP]
> Your Cerebral SVD Dashboard monitoring setup is complete! You can now monitor container performance and track unique visitors accessing the SVD Dashboard.

---

## Troubleshooting

### Grafana Login Issues

**Problem:** Cannot login with default credentials.

**Solution:** Retrieve the password from the Kubernetes secret:

```bash
kubectl get secret prometheus-grafana -n monitoring \
    -o jsonpath="{.data.admin-password}" | base64 --decode; echo
```

### VictoriaLogs Not Receiving Logs

**Problem:** No logs appear in VictoriaLogs.

**Solution:** Check Vector logs:

```bash
kubectl get pods -n monitoring | grep vector
kubectl logs -n monitoring victoria-logs-vector-xxxxx --tail=50
```

### Only Seeing Internal IPs

**Problem:** `client_ip` shows cluster IPs instead of real IPs.

**Solution:** Ensure `externalTrafficPolicy: Local` is set:

```bash
kubectl patch svc ingress-nginx-controller -n ingress-nginx \
    -p '{"spec":{"externalTrafficPolicy":"Local"}}'
```

---

## Maintenance and Operations

### Upgrading the Stack

Upgrade kube-prometheus-stack:

```bash
helm repo update
helm upgrade prometheus prometheus-community/kube-prometheus-stack \
    --namespace monitoring
```

Upgrade VictoriaLogs:

```bash
helm upgrade victoria-logs vm/victoria-logs-single \
    --namespace monitoring \
    -f victorialogs-values.yaml
```

### Scaling for Production

| Component | Small (< 10 nodes) | Medium (10–50 nodes) |
|-----------|-------------------|---------------------|
| VictoriaLogs CPU | 200m–1 core | 2–4 cores |
| VictoriaLogs Memory | 256MB–1GB | 2–4GB |
| VictoriaLogs Storage | 50GB | 200–500GB |
| Prometheus CPU | 500m–1 core | 2–4 cores |
| Prometheus Memory | 1–2GB | 4–8GB |
| Prometheus Storage | 50GB | 200–500GB |

---

## Kubernetes Configuration Files

This chapter documents the Kubernetes configuration files used to deploy the Cerebral SVD Dashboard and its monitoring infrastructure.

### NGINX Ingress ConfigMap

The NGINX Ingress Controller requires specific configuration to preserve real client IP addresses and output logs in JSON format for parsing by VictoriaLogs.

<details>
<summary>Click to expand nginx-ingress-configmap.yaml</summary>

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: ingress-nginx-controller
  namespace: ingress-nginx
data:
  # Preserve real client IP
  use-forwarded-headers: "true"
  compute-full-forwarded-for: "true"
  enable-real-ip: "true"

  # JSON log format
  log-format-escape-json: "true"
  log-format-upstream: |
    {
      "timestamp": "$time_iso8601",
      "remote_addr": "$remote_addr",
      "x_forwarded_for": "$proxy_add_x_forwarded_for",
      "method": "$request_method",
      "request_uri": "$request_uri",
      "status": $status,
      "body_bytes_sent": $body_bytes_sent,
      "request_time": $request_time,
      "http_referer": "$http_referer",
      "http_user_agent": "$http_user_agent"
    }
```

</details>

**Key Configuration Options:**

- `use-forwarded-headers`: Enables processing of `X-Forwarded-*` headers
- `compute-full-forwarded-for`: Preserves the full chain of proxy IPs
- `enable-real-ip`: Extracts the real client IP from proxy headers
- `log-format-escape-json`: Ensures proper JSON escaping in logs
- `log-format-upstream`: Custom JSON log format for Vector parsing

Apply with:

```bash
kubectl apply -f nginx-ingress-configmap.yaml
kubectl rollout restart deployment ingress-nginx-controller -n ingress-nginx
```

### RShiny Application Deployment

The RShiny Dashboard application is deployed as a Kubernetes Deployment with an associated Service.

<details>
<summary>Click to expand rshiny-deployment.yaml</summary>

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rshiny-dashboard
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: shiny
  template:
    metadata:
      labels:
        app: shiny
    spec:
      containers:
        - name: rshiny-dashboard
          image: rshiny-dashboard:latest
          imagePullPolicy: Never
          ports:
            - containerPort: 3838
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
---
apiVersion: v1
kind: Service
metadata:
  name: rshiny-dashboard
  namespace: default
spec:
  selector:
    app: shiny
  ports:
    - port: 3838
      targetPort: 3838
```

</details>

**Resource Configuration:**

- **CPU**: Requests 100m (0.1 cores), limited to 500m (0.5 cores)
- **Memory**: Requests 256Mi, limited to 512Mi
- **Port**: Shiny Server default port 3838
- `imagePullPolicy: Never`: Uses locally built Docker image

### RShiny Ingress

The Ingress resource routes external HTTP traffic to the RShiny application.

<details>
<summary>Click to expand rshiny-ingress.yaml</summary>

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: rshiny-ingress
  namespace: default
spec:
  ingressClassName: nginx
  rules:
    - host: shiny.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: rshiny-dashboard
                port:
                  number: 3838
```

</details>

**Configuration Details:**

- `ingressClassName: nginx`: Uses the NGINX Ingress Controller
- `host: shiny.local`: Local hostname (add to `/etc/hosts` for development)
- `pathType: Prefix`: Matches all paths starting with `/`

### Grafana Image Renderer

The Grafana Image Renderer enables exporting dashboard panels as PNG or PDF images.

<details>
<summary>Click to expand grafana-renderer.yaml</summary>

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grafana-image-renderer
  namespace: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app: grafana-image-renderer
  template:
    metadata:
      labels:
        app: grafana-image-renderer
    spec:
      containers:
        - name: renderer
          image: grafana/grafana-image-renderer:latest
          ports:
            - containerPort: 8081
          env:
            - name: ENABLE_METRICS
              value: "true"
---
apiVersion: v1
kind: Service
metadata:
  name: grafana-image-renderer
  namespace: monitoring
spec:
  selector:
    app: grafana-image-renderer
  ports:
    - port: 8081
      targetPort: 8081
```

</details>

Configure Grafana to use the renderer by adding these environment variables to the Grafana deployment:

```yaml
env:
  - name: GF_RENDERING_SERVER_URL
    value: http://grafana-image-renderer:8081/render
  - name: GF_RENDERING_CALLBACK_URL
    value: http://prometheus-grafana:80/
```

---

## Grafana Dashboard Configuration

The Combined Dashboard provides unified monitoring for both the RShiny application and the host operating system.

### Dashboard Overview

| Property | Value |
|----------|-------|
| Dashboard UID | `combined-rshiny-hostos` |
| Title | Combined Dashboard - RShiny & Host OS Monitoring |
| Schema Version | 42 |
| Timezone | America/Toronto |
| Refresh | Auto |

### Data Sources

The dashboard uses two data sources:

1. **VictoriaMetrics Logs Datasource** (UID: `ff70ntmxr0074a`)
   - Used for visitor tracking and log-based metrics
   - Query language: LogsQL

2. **Prometheus** (UID: `prometheus`)
   - Used for container and host OS metrics
   - Query language: PromQL

### Panel Organization

The dashboard is organized into five row sections:

| Section | Panels | Description |
|---------|--------|-------------|
| RShiny Application | 4 | Unique visitors, visitors over time, container memory/CPU |
| Host OS - CPU | 2 | CPU usage (stacked), load averages (1m, 5m, 15m) |
| Host OS - Memory | 2 | Memory usage timeseries, memory usage gauge |
| Host OS - Disk | 2 | Disk I/O (read/write/io time), disk space table |
| Host OS - Network | 2 | Network received/transmitted (bits/s) |

### Template Variables

The dashboard defines three template variables:

| Variable | Type | Description |
|----------|------|-------------|
| `$datasource` | datasource | Prometheus datasource selector |
| `$cluster` | query | Cluster label (hidden, auto-populated) |
| `$instance` | query | Node exporter instance selector |

### Key Queries

#### Unique Visitors (LogsQL)

```
_time:1d kubernetes.container_name:controller
  | stats count_uniq(client_ip) as unique_visitors
```

#### Container Memory Usage (PromQL)

```
sum(container_memory_working_set_bytes{pod=~"rshiny.*"}) by (pod)
```

#### Host CPU Usage (PromQL)

```
(1 - sum without (mode) (rate(node_cpu_seconds_total{job="node-exporter",
  mode=~"idle|iowait|steal", instance="$instance"}[$__rate_interval])))
/ ignoring(cpu) group_left count without (cpu, mode)
  (node_cpu_seconds_total{job="node-exporter", mode="idle", instance="$instance"})
```

#### Disk I/O (PromQL)

```
rate(node_disk_read_bytes_total{job="node-exporter", instance="$instance",
  device=~"(/dev/)?(nvme.+|sd.+|vd.+|dm-.+)"}[$__rate_interval])
```

### Importing the Dashboard

The dashboard JSON file is located at:

```
monitoring/json/Combined Dashboard - RShiny & Host OS Monitoring.json
```

To import:

1. Open Grafana and navigate to **Dashboards** → **Import**
2. Upload the JSON file or paste its contents
3. Select the appropriate Prometheus and VictoriaLogs data sources
4. Click **Import**

---

## Appendix: Glossary

| Term | Definition |
|------|------------|
| cSVD | Cerebral Small Vessel Disease |
| GWAS | Genome-Wide Association Study |
| PWAS | Proteome-Wide Association Study |
| EWAS | Epigenome-Wide Association Study |
| TWAS | Transcriptome-Wide Association Study |
| OMIM | Online Mendelian Inheritance in Man |
| GO | Gene Ontology |
| NCBI | National Center for Biotechnology Information |
| PMID | PubMed Identifier |
| QS | Quick Serialization — R package for 3-5x faster data loading than RDS |
| fastmap | R package providing O(1) hash map implementation |
| data.table | High-performance R data structure |
| Memoization | Caching function results for repeated calls |
| Debouncing | Delaying reactive updates to prevent excessive computation |
| CRD | Custom Resource Definition (Kubernetes) |
| DaemonSet | Kubernetes workload that runs one pod per node |
| LogsQL | Query language used by VictoriaLogs |
| PromQL | Query language used by Prometheus |
| ServiceMonitor | Prometheus Operator CRD for metric scraping |

---

## Appendix: Quick Reference Card

### Common R Commands

```
# Development
devtools::load_all()        # Load package for testing
devtools::document()        # Regenerate documentation
devtools::check()           # Full package check
devtools::test()            # Run tests
devtools::install()         # Install locally

# Documentation
?function_name              # View help
roxygen2::roxygenise()      # Generate docs

# Linting
lintr::lint_package()       # Check code style
styler::style_pkg()         # Auto-format code

# Testing
source("tests/test_all.R")  # Run all tests
covr::file_coverage("tests/test_all.R")  # Test coverage
```

### Common Kubernetes Commands

```bash
# Viewing Logs
kubectl logs -n monitoring prometheus-prometheus-kube-prometheus-prometheus-0
kubectl logs -n monitoring deployment/prometheus-grafana -c grafana
kubectl logs -n monitoring victoria-logs-victoria-logs-single-server-0

# Port Forwarding
kubectl port-forward svc/prometheus-grafana 3000:80 -n monitoring
kubectl port-forward svc/prometheus-kube-prometheus-prometheus 9090:9090 -n monitoring
kubectl port-forward svc/victoria-logs-victoria-logs-single-server 9428:9428 -n monitoring

# Checking Resources
kubectl get pods -n monitoring
kubectl get svc -n monitoring
kubectl get pvc -n monitoring
kubectl get servicemonitors -n monitoring
```
