@Kiro Act as a Principal Cloud-Native Architect & Kubernetes Specialist.

I want to deploy a complete, end-to-end Enterprise AI Assistant & Microservices Sandbox on a local Kubernetes cluster (like KinD or Minikube). 

Please generate a complete set of Kubernetes manifests, Helm charts or Kustomize setups, and the corresponding Python application code for a full platform containing the following pods/services:

1. CORE INFRASTRUCTURE & IAM:
   - Keycloak Deployment & Service (with PostgreSQL backend) for OAuth2 / OIDC token issuance and validation.
   - Vector Store (Qdrant or Pgvector) for the Hybrid RAG engine.

2. ENTERPRISE SIMULATION ASSETS:
   - `git-codebase-mock`: A light HTTP container / volume server hosting sample Python microservices source code (including an Order Management Service and a PII encryption utility).
   - `doc-site`: A static Nginx container serving markdown/HTML documentation pages (architecture records, runbooks, onboarding guides).

3. OBSERVABILITY STACK:
   - Prometheus & Grafana (pre-configured to scrape cluster metrics and order management service endpoints).
   - Apache Skywalking (OAP server and UI) configured for application performance monitoring (APM) and distributed tracing.

4. SAMPLE WORKLOAD (Order Management Service):
   - A Python FastAPI `order-service` that handles orders, validates tokens via Keycloak, and emits Prometheus metrics and Skywalking traces.
   - Purposefully inject a simulated error route or latency spike so telemetry tools capture it.

5. THE MCP SERVER (Python + FastMCP):
   - Deployed as a Kubernetes Deployment running a FastMCP server over Streamable HTTP / SSE.
   - Exposes MCP Tools and Resources:
     a. `search_codebase`: Hybrid RAG retriever querying the Git codebase mock and Doc-site storage.
     b. `get_cluster_telemetry`: Tool that queries Prometheus and Skywalking APIs to fetch error spikes and traces for the `order-service`.
     c. `validate_iam_access`: Tool checking Keycloak token scopes and RBAC rules.
   - Secured behind a ServiceAccount with RBAC and Keycloak token verification middleware.

Please generate:
- Directory structure (`deployments/`, `mcp-server/`, `order-service/`, `manifests/`).
- Kubernetes YAML manifests (`namespace.yaml`, `keycloak.yaml`, `observability.yaml`, `order-service.yaml`, `mcp-server.yaml`).
- The Python implementation for the FastMCP server and the Order Management service.
- A single `deploy.sh` script using `kubectl` or `helm` to bootstrap everything locally.


+-----------------------------------------------------------------------------------+
|                           KUBERNETES CLUSTER (KinD / Minikube)                    |
|                                                                                   |
|  +--------------------+   +-------------------+   +----------------------------+  |
|  |   Keycloak Pod     |   | Qdrant Vector DB  |   | Git & Doc Site Pods        |  |
|  |  (IAM / Auth)      |   | (RAG Embeddings)  |   | (Source Code & Runbooks)   |  |
|  +---------+----------+   +---------+---------+   +--------------+-------------+  |
|            |                        |                            |                |
|            +------------------------+----------------------------+                |
|                                     |                                             |
|  +----------------------------------v------------------------------------------+  |
|  |                       Order Management Service (FastAPI)                    |  |
|  |  - Emits Prometheus metrics & Skywalking traces                             |  |
|  +----------------------------------+------------------------------------------+  |
|                                     |                                             |
|  +----------------------------------v------------------------------------------+  |
|  |                           Observability Tier                                |  |
|  |      [Prometheus] <---> [Grafana] <---> [Apache Skywalking OAP]             |  |
|  +----------------------------------+------------------------------------------+  |
|                                     |                                             |
|  +----------------------------------v------------------------------------------+  |
|  |                         MCP Server Gateway Deployment                       |  |
|  |  - Python FastMCP (Streamable HTTP / SSE)                                   |  |
|  |  - Unified Tools: Code Search, Telemetry Fetch, IAM Validation              |  |
|  +----------------------------------+------------------------------------------+  |
+-------------------------------------|---------------------------------------------+
                                      |
                           Model Context Protocol (MCP)
                                      |
                     [Claude Desktop / Cursor / IDE Client]