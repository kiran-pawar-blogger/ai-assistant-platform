# 🧠 Org-Mind — Enterprise AI Assistant & Microservices Sandbox

A complete, locally deployable Kubernetes platform that combines an **IAM-secured Order Management Service**, a full **Observability stack**, and a **FastMCP server** exposing AI tools for code search, telemetry fetching, and IAM validation — all running on a local [KinD](https://kind.sigs.k8s.io/) cluster.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    KUBERNETES CLUSTER (KinD)  namespace: org-mind        │
│                                                                          │
│  ┌─────────────────┐  ┌──────────────────┐  ┌──────────────────────┐   │
│  │  Keycloak (IAM) │  │  Qdrant (Vector) │  │  git-mock + doc-site │   │
│  │  + PostgreSQL   │  │  RAG Embeddings  │  │  Source & Runbooks   │   │
│  └────────┬────────┘  └────────┬─────────┘  └──────────┬───────────┘   │
│           │                    │                        │               │
│  ┌────────▼────────────────────▼────────────────────────▼───────────┐  │
│  │              Order Management Service (FastAPI)                   │  │
│  │    JWT auth · Prometheus metrics · Skywalking traces              │  │
│  │    /orders/error-demo  ← intentional 500 for telemetry demo       │  │
│  └────────────────────────────┬──────────────────────────────────────┘  │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐  │
│  │                     Observability Tier                            │  │
│  │    Prometheus ←→ Grafana        Apache Skywalking OAP + UI        │  │
│  └────────────────────────────┬──────────────────────────────────────┘  │
│                               │                                          │
│  ┌────────────────────────────▼──────────────────────────────────────┐  │
│  │                  MCP Server (FastMCP 2.x)                         │  │
│  │    search_codebase · get_cluster_telemetry · validate_iam_access  │  │
│  └────────────────────────────┬──────────────────────────────────────┘  │
└────────────────────────────────┼────────────────────────────────────────┘
                                 │  Model Context Protocol (HTTP/SSE)
                    ┌────────────▼────────────┐
                    │  Claude Desktop / Cursor │
                    └─────────────────────────┘
```

---

## Repository Layout

```
org-mind/
├── deploy.ps1                    # Windows PowerShell bootstrap (recommended on Windows)
├── deploy.sh                     # Bash bootstrap (WSL2 / Git Bash / Linux / macOS)
│
├── manifests/                    # Kubernetes YAML manifests
│   ├── namespace.yaml            # Namespace, RBAC, ServiceAccount, platform-config
│   ├── keycloak.yaml             # Keycloak 23 + PostgreSQL 15 + realm bootstrap
│   ├── qdrant.yaml               # Qdrant v1.7.4 vector store
│   ├── simulation.yaml           # git-codebase-mock + doc-site
│   ├── observability.yaml        # Prometheus + Grafana (pre-wired dashboard)
│   ├── skywalking.yaml           # Apache Skywalking OAP + UI
│   ├── order-service.yaml        # Order Management Service deployment
│   └── mcp-server.yaml           # FastMCP server deployment
│
├── order-service/                # Order Management Service (Python)
│   ├── main.py                   # FastAPI app with auth, metrics, traces
│   ├── Dockerfile
│   └── requirements.txt
│
├── mcp-server/                   # MCP Gateway (Python)
│   ├── main.py                   # FastMCP server with 3 tools
│   ├── init_collections.py       # Standalone Qdrant collection re-init script
│   ├── Dockerfile
│   └── requirements.txt
│
├── git-codebase-mock/            # Source files served over HTTP for RAG ingestion
│   └── sample-code/
│       ├── order_management_service.py
│       └── pii_encryption_utility.py
│
└── doc-site/                     # Documentation served by Nginx for RAG ingestion
    └── html/
        ├── index.html
        ├── architecture.html     # Architecture Decision Records
        ├── runbooks.html         # Operational runbooks
        ├── onboarding.html       # Developer onboarding guide
        └── api-reference.html    # Order Service API reference
```

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Docker Desktop | 4.x+ | https://docs.docker.com/get-docker/ |
| KinD | 0.20+ | https://kind.sigs.k8s.io/docs/user/quick-start/ |
| kubectl | 1.28+ | https://kubernetes.io/docs/tasks/tools/ |
| Python | 3.11+ | https://www.python.org/downloads/ (for local dev only) |

> **Windows users:** PowerShell 5.1+ is sufficient. No WSL2 required — use `deploy.ps1`.

---

## Quick Start

### Windows (PowerShell) — recommended

Open **PowerShell** (not bash, not cmd) and run:

```powershell
cd d:\dev\AI\org-mind

# First time only — allow local scripts to run
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

# Deploy everything (builds images, creates KinD cluster, applies manifests)
.\deploy.ps1

# Skip image rebuilds if already loaded into KinD
.\deploy.ps1 -SkipBuild

# Tear everything down
.\deploy.ps1 -Teardown
```

> ⚠️ **Do not run `bash deploy.ps1`** — `.ps1` is a PowerShell script, not a bash script.
> Use `.\deploy.ps1` directly in PowerShell.

### Windows (Git Bash) — alternative

If you prefer bash, open **Git Bash** (not PowerShell, not WSL) and run:

```bash
cd /d/dev/AI/org-mind
bash deploy.sh
```

### Linux / macOS / WSL2

```bash
cd /path/to/org-mind
bash deploy.sh
```

The script will:
1. Check prerequisites (`docker`, `kubectl`, `kind`)
2. Create a KinD cluster with all NodePort mappings pre-configured
3. Build the two custom Docker images (`order-service`, `mcp-server`)
4. Load them into KinD with `kind load docker-image`
5. Apply all 8 Kubernetes manifests in dependency order
6. Wait for every deployment to roll out (5 min timeout each)
7. Run smoke tests against all 7 services
8. Fetch a demo Keycloak token and exercise the authenticated Order API

---

## Service URLs

Once deployed, all services are accessible on `localhost` via NodePort:

| Service | URL | Credentials |
|---------|-----|-------------|
| Keycloak Admin Console | http://localhost:30080/admin | `admin` / `admin_pass_2024` |
| Grafana Dashboards | http://localhost:30300 | `admin` / `grafana_pass_2024` |
| Prometheus | http://localhost:30909 | — |
| Skywalking UI | http://localhost:30880 | — |
| Order Service (Swagger) | http://localhost:30800/docs | Bearer token required |
| MCP Server | http://localhost:30808/mcp | Bearer token required |
| Qdrant Dashboard | http://localhost:30633/dashboard | — |

---

## Getting a Token

All authenticated endpoints require a Keycloak JWT. Fetch one with:

**PowerShell:**
```powershell
$body = @{
    client_id     = "order-service"
    client_secret = "order-service-secret-2024"
    username      = "testuser"
    password      = "Test@1234"
    grant_type    = "password"
}
$r = Invoke-RestMethod -Uri "http://localhost:30080/realms/org-mind/protocol/openid-connect/token" -Method Post -Body $body
$TOKEN = $r.access_token
```

**Bash / curl:**
```bash
TOKEN=$(curl -s -X POST http://localhost:30080/realms/org-mind/protocol/openid-connect/token \
  -d client_id=order-service \
  -d client_secret=order-service-secret-2024 \
  -d username=testuser \
  -d password=Test@1234 \
  -d grant_type=password | python -m json.tool | grep access_token | cut -d'"' -f4)
```

Two test users are pre-created in the `org-mind` realm:

| Username | Password | Roles |
|----------|----------|-------|
| `testuser` | `Test@1234` | `order-reader`, `order-writer` |
| `admin-user` | `Admin@1234` | `admin`, `order-reader`, `order-writer` |

---

## Components

### 1. Keycloak (IAM / OIDC)

Keycloak 23 runs with a PostgreSQL 15 backend. On first boot it auto-imports the `org-mind` realm containing:
- Two OAuth2 clients: `order-service` and `mcp-server`
- Three realm roles: `order-reader`, `order-writer`, `admin`
- Two pre-created test users

The realm JSON lives in `manifests/keycloak.yaml` as a ConfigMap and is picked up by Keycloak's `--import-realm` flag at startup.

### 2. Qdrant (Vector Store)

Qdrant v1.7.4 provides the hybrid RAG backend. The MCP server creates a collection named `org-mind-codebase` at startup and ingests:
- Source files from `git-codebase-mock` (`order_management_service.py`, `pii_encryption_utility.py`)
- Documentation pages from `doc-site` (architecture, runbooks, onboarding, API reference)

The default embedding is a deterministic pseudo-embedding (for sandbox use). Swap `_naive_embed()` in `mcp-server/main.py` with a real sentence-transformer for semantic accuracy.

### 3. Order Management Service

FastAPI service demonstrating a production-grade pattern:
- **JWT validation** against Keycloak JWKS (cached, auto-refreshed)
- **Role enforcement** (`order-reader` / `order-writer`) via FastAPI dependencies
- **Prometheus metrics** exported at `/metrics`:
  - `order_service_requests_total` — Counter by method/endpoint/status
  - `order_service_request_duration_seconds` — Histogram (p50/p95/p99)
  - `order_service_errors_total` — Counter of 5xx responses
  - `order_service_active_orders` — Gauge
- **Skywalking distributed tracing** via `apache-skywalking-agent` wrapping uvicorn
- Two intentional demo routes for telemetry testing:

| Route | Behaviour |
|-------|-----------|
| `GET /orders/error-demo` | 2 s delay then HTTP 500 — triggers Grafana error alert |
| `GET /orders/slow-demo` | 3 s delay (configurable) — triggers Skywalking slow trace |

### 4. Observability Stack

**Prometheus** auto-discovers pods via annotations:
```yaml
prometheus.io/scrape: "true"
prometheus.io/port:   "8000"
prometheus.io/path:   "/metrics"
```
Alert rules are pre-configured for error rate > 10% and p95 latency > 2 s.

**Grafana** ships with a pre-provisioned *Order Service Dashboard* showing request rate, error rate, latency percentiles, and active order count. The Prometheus datasource is wired in automatically — no manual setup needed.

**Apache Skywalking** (OAP 9.7.0) uses in-memory H2 storage, suitable for the sandbox. It receives traces from `order-service` over gRPC port 11800.

### 5. MCP Server

FastMCP 2.x server mounted at `/mcp` over Streamable HTTP. Every request to `/mcp/*` must carry a valid Keycloak Bearer token (validated before the tool runs).

#### Tools

**`search_codebase(query, top_k=5)`**
Performs a vector search in Qdrant over the ingested codebase and documentation. Returns filenames, sources, relevance scores, and 500-character excerpts.

```json
{
  "query": "how are PII fields encrypted?",
  "results": [
    {
      "filename": "pii_encryption_utility.py",
      "source": "git-codebase-mock",
      "score": 0.9821,
      "excerpt": "def encrypt_field(plaintext: str) -> str: ..."
    }
  ]
}
```

**`get_cluster_telemetry(time_range="5m", include_traces=true)`**
Queries Prometheus for live order-service metrics and Skywalking for recent error/slow traces.

```json
{
  "metrics": {
    "request_rate_per_sec": "12.4",
    "error_rate_per_sec": "0.83",
    "p95_latency_sec": "2.14",
    "active_orders": "7"
  },
  "alert": "⚠ ERROR SPIKE DETECTED",
  "traces": {
    "total_error_traces": 3,
    "sample_traces": [...]
  }
}
```

**`validate_iam_access(token, required_roles=[])`**
Decodes and validates a JWT, returning the subject, roles, scopes, and an explicit access-granted check.

```json
{
  "valid": true,
  "subject": "a1b2c3d4-...",
  "preferred_username": "testuser",
  "roles": ["order-reader", "order-writer"],
  "required_roles_check": {
    "required": ["order-writer"],
    "missing": [],
    "access_granted": true
  }
}
```

---

## Connecting Claude Desktop / Cursor

Add this to your Claude Desktop config (`%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "org-mind": {
      "url": "http://localhost:30808/mcp",
      "headers": {
        "Authorization": "Bearer <paste-your-token-here>"
      }
    }
  }
}
```

Restart Claude Desktop. The three tools (`search_codebase`, `get_cluster_telemetry`, `validate_iam_access`) will appear in the tool list.

---

## Triggering Telemetry Demos

With a valid token, hit the intentional error/slow routes to generate data in Grafana and Skywalking:

```powershell
# Trigger 10 errors (watch Grafana error panel spike)
1..10 | ForEach-Object {
    Invoke-WebRequest -Uri "http://localhost:30800/orders/error-demo" `
        -Headers @{ Authorization = "Bearer $TOKEN" } -ErrorAction SilentlyContinue
}

# Trigger slow traces (check Skywalking for slow-trace alerts)
1..5 | ForEach-Object {
    Invoke-WebRequest -Uri "http://localhost:30800/orders/slow-demo" `
        -Headers @{ Authorization = "Bearer $TOKEN" }
}
```

Then ask the MCP server via Claude:
> *"Check the current telemetry for the order service — are there any error spikes?"*

---

## Useful kubectl Commands

```powershell
# Watch all pods
kubectl get pods -n org-mind -w

# Tail logs
kubectl logs -f deployment/order-service  -n org-mind
kubectl logs -f deployment/mcp-server     -n org-mind
kubectl logs -f deployment/keycloak       -n org-mind
kubectl logs -f deployment/skywalking-oap -n org-mind

# Describe a failing pod
kubectl describe pod -l app=order-service -n org-mind

# Re-initialise Qdrant collections manually
kubectl exec -it deployment/mcp-server -n org-mind -- python /app/init_collections.py

# Scale order-service
kubectl scale deployment/order-service --replicas=3 -n org-mind

# Reload Prometheus config without restart
curl -X POST http://localhost:30909/-/reload
```

---

## Configuration Reference

All inter-service URLs are centralised in the `platform-config` ConfigMap (`manifests/namespace.yaml`) and injected into every pod via `envFrom`. Override any value with a patch or by editing the ConfigMap:

| Key | Default |
|-----|---------|
| `KEYCLOAK_URL` | `http://keycloak.org-mind.svc.cluster.local:8080` |
| `KEYCLOAK_REALM` | `org-mind` |
| `QDRANT_URL` | `http://qdrant.org-mind.svc.cluster.local:6333` |
| `PROMETHEUS_URL` | `http://prometheus.org-mind.svc.cluster.local:9090` |
| `SKYWALKING_OAP_URL` | `http://skywalking-oap.org-mind.svc.cluster.local:12800` |
| `ORDER_SERVICE_URL` | `http://order-service.org-mind.svc.cluster.local:8000` |
| `GIT_MOCK_URL` | `http://git-codebase-mock.org-mind.svc.cluster.local:8080` |
| `DOC_SITE_URL` | `http://doc-site.org-mind.svc.cluster.local:80` |

### Order Service environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ERROR_DEMO_ENABLED` | `true` | Enable the `/orders/error-demo` 500 route |
| `SLOW_DEMO_SECONDS` | `3` | Sleep duration for `/orders/slow-demo` |
| `TOKEN_AUDIENCE` | `account` | Expected JWT `aud` claim |

---

## Troubleshooting

**Pod stuck in `Init:CrashLoopBackOff`**
All initContainers use `/dev/tcp` TCP probes (no `nc` dependency). If you see this, check the init container logs:
```powershell
kubectl logs -n org-mind <pod-name> -c wait-for-keycloak
```

**Keycloak realm not created**
The realm imports on first boot only (fresh DB). If Keycloak restarted against an existing DB, the import is silently skipped — which is correct. Verify the realm exists:
```
http://localhost:30080/realms/org-mind/.well-known/openid-configuration
```

**`ErrImagePull` on `org-mind/order-service` or `org-mind/mcp-server`**
The custom images weren't loaded into KinD. Run:
```powershell
kind load docker-image org-mind/order-service:latest --name org-mind
kind load docker-image org-mind/mcp-server:latest    --name org-mind
```

**Skywalking UI shows no services**
OAP takes ~60 s to initialise. Wait for the readiness probe to pass, then generate some traffic on the order service to produce traces.

**Qdrant collection missing / search returns empty**
Re-run the init script:
```powershell
kubectl exec -it deployment/mcp-server -n org-mind -- python /app/init_collections.py
```

**MCP tool returns `Token invalid`**
Tokens from Keycloak expire after 5 minutes by default. Fetch a fresh token and update the `Authorization` header in your IDE client config.

---

## Production Graduation Checklist

This sandbox uses several shortcuts intentional for local development. Before promoting to production:

- [ ] Replace H2 in-memory storage in Skywalking OAP with Elasticsearch
- [ ] Replace the pseudo-embedding in `mcp-server/main.py` with a real sentence-transformer (`all-MiniLM-L6-v2` or similar)
- [ ] Store all secrets (`keycloak-db-secret`, `keycloak-admin-secret`, `grafana-admin-secret`) in Vault or a KMS — remove `stringData` from manifests
- [ ] Add mTLS between services (Istio or Linkerd)
- [ ] Switch Keycloak from `start-dev` to `start` with TLS configured
- [ ] Set Keycloak token lifetimes appropriate for your security policy
- [ ] Replace `imagePullPolicy: IfNotPresent` with a pinned digest from a private registry
- [ ] Add PodDisruptionBudgets and HorizontalPodAutoscalers for production workloads
- [ ] Replace the in-memory order store in `order-service/main.py` with a persistent database

---

## License

MIT — free to use, modify, and distribute.
