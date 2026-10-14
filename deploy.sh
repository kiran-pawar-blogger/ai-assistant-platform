#!/usr/bin/env bash
# =============================================================================
#  deploy.sh – Org-Mind Enterprise AI & Microservices Sandbox Bootstrap
#  Supports KinD and Minikube.  Run from the project root directory.
#
#  Usage:
#    bash deploy.sh [--kind | --minikube] [--skip-build] [--teardown]
#
#  Flags:
#    --kind        Use KinD cluster (default)
#    --minikube    Use Minikube cluster
#    --skip-build  Skip Docker image builds (use if images already loaded)
#    --teardown    Delete the cluster and all resources
# =============================================================================
set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}══════════════════════════════════════════${RESET}"; \
            echo -e "${BOLD}  $*${RESET}"; \
            echo -e "${BOLD}══════════════════════════════════════════${RESET}"; }

# ── Defaults ──────────────────────────────────────────────────────────────────
CLUSTER_TYPE="kind"
SKIP_BUILD=false
TEARDOWN=false
CLUSTER_NAME="org-mind"
NAMESPACE="org-mind"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Arg parsing ───────────────────────────────────────────────────────────────
for arg in "$@"; do
  case $arg in
    --kind)       CLUSTER_TYPE="kind"      ;;
    --minikube)   CLUSTER_TYPE="minikube"  ;;
    --skip-build) SKIP_BUILD=true          ;;
    --teardown)   TEARDOWN=true            ;;
    *) warn "Unknown argument: $arg"       ;;
  esac
done

# ── Teardown ─────────────────────────────────────────────────────────────────
if $TEARDOWN; then
  step "Tearing down cluster: $CLUSTER_NAME"
  if [ "$CLUSTER_TYPE" = "kind" ]; then
    kind delete cluster --name "$CLUSTER_NAME" && success "KinD cluster deleted"
  else
    minikube delete --profile "$CLUSTER_NAME" && success "Minikube cluster deleted"
  fi
  exit 0
fi

# ── Prerequisite checks ───────────────────────────────────────────────────────
step "Checking prerequisites"
for cmd in docker kubectl; do
  command -v "$cmd" &>/dev/null || error "'$cmd' not found. Please install it first."
  success "$cmd found"
done

if [ "$CLUSTER_TYPE" = "kind" ]; then
  command -v kind &>/dev/null || error "'kind' not found. Install: https://kind.sigs.k8s.io/docs/user/quick-start/"
  success "kind found"
else
  command -v minikube &>/dev/null || error "'minikube' not found. Install: https://minikube.sigs.k8s.io/docs/start/"
  success "minikube found"
fi

# ── Create / reuse cluster ────────────────────────────────────────────────────
step "Cluster setup ($CLUSTER_TYPE)"
if [ "$CLUSTER_TYPE" = "kind" ]; then
  if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    warn "KinD cluster '${CLUSTER_NAME}' already exists – reusing."
  else
    info "Creating KinD cluster '${CLUSTER_NAME}'…"
    cat <<EOF | kind create cluster --name "$CLUSTER_NAME" --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      # Keycloak
      - containerPort: 30080
        hostPort: 30080
        protocol: TCP
      # Grafana
      - containerPort: 30300
        hostPort: 30300
        protocol: TCP
      # Prometheus
      - containerPort: 30909
        hostPort: 30909
        protocol: TCP
      # Skywalking UI
      - containerPort: 30880
        hostPort: 30880
        protocol: TCP
      # Order Service
      - containerPort: 30800
        hostPort: 30800
        protocol: TCP
      # MCP Server
      - containerPort: 30808
        hostPort: 30808
        protocol: TCP
      # Qdrant
      - containerPort: 30633
        hostPort: 30633
        protocol: TCP
EOF
    success "KinD cluster created"
  fi
else
  if minikube status --profile "$CLUSTER_NAME" 2>/dev/null | grep -q "Running"; then
    warn "Minikube profile '${CLUSTER_NAME}' already running – reusing."
  else
    info "Starting Minikube profile '${CLUSTER_NAME}'…"
    minikube start \
      --profile "$CLUSTER_NAME" \
      --driver=docker \
      --cpus=4 \
      --memory=6144 \
      --kubernetes-version=v1.28.0
    success "Minikube started"
  fi
  eval "$(minikube -p "$CLUSTER_NAME" docker-env)"
  info "Docker env set to Minikube daemon"
fi

# ── Build Docker images ───────────────────────────────────────────────────────
step "Building Docker images"
if $SKIP_BUILD; then
  warn "--skip-build set; skipping image builds"
else
  info "Building order-service image…"
  docker build \
    -t org-mind/order-service:latest \
    -f "${SCRIPT_DIR}/order-service/Dockerfile" \
    "${SCRIPT_DIR}/order-service"
  success "order-service image built"

  info "Building mcp-server image…"
  docker build \
    -t org-mind/mcp-server:latest \
    -f "${SCRIPT_DIR}/mcp-server/Dockerfile" \
    "${SCRIPT_DIR}/mcp-server"
  success "mcp-server image built"

  # Load images into KinD (Minikube uses its own Docker daemon so no load needed)
  if [ "$CLUSTER_TYPE" = "kind" ]; then
    info "Loading images into KinD…"
    kind load docker-image org-mind/order-service:latest --name "$CLUSTER_NAME"
    kind load docker-image org-mind/mcp-server:latest   --name "$CLUSTER_NAME"
    success "Images loaded into KinD"
  fi
fi

# ── Deploy manifests ──────────────────────────────────────────────────────────
step "Applying Kubernetes manifests"
MANIFESTS=(
  "manifests/namespace.yaml"
  "manifests/keycloak.yaml"
  "manifests/qdrant.yaml"
  "manifests/simulation.yaml"
  "manifests/observability.yaml"
  "manifests/skywalking.yaml"
  "manifests/order-service.yaml"
  "manifests/mcp-server.yaml"
)

for manifest in "${MANIFESTS[@]}"; do
  info "Applying ${manifest}…"
  kubectl apply -f "${SCRIPT_DIR}/${manifest}"
  success "Applied ${manifest}"
done

# ── Wait for critical pods ────────────────────────────────────────────────────
step "Waiting for pods to become Ready"
DEPLOYMENTS=(
  "keycloak-postgres"
  "keycloak"
  "qdrant"
  "git-codebase-mock"
  "doc-site"
  "prometheus"
  "grafana"
  "skywalking-oap"
  "skywalking-ui"
  "order-service"
  "mcp-server"
)

for deploy in "${DEPLOYMENTS[@]}"; do
  info "Waiting for deployment/${deploy}…"
  kubectl rollout status deployment/"${deploy}" \
    -n "$NAMESPACE" \
    --timeout=300s \
    || warn "deployment/${deploy} did not become ready within 5 min – check: kubectl describe deployment/${deploy} -n ${NAMESPACE}"
done

# ── Smoke tests ───────────────────────────────────────────────────────────────
step "Running smoke tests"

_smoke() {
  local label=$1
  local url=$2
  local expected_code=${3:-200}
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$url" 2>/dev/null || echo "000")
  if [ "$code" = "$expected_code" ]; then
    success "${label}: HTTP ${code}"
  else
    warn "${label}: expected HTTP ${expected_code}, got ${code}  (${url})"
  fi
}

# Give NodePorts a moment to stabilise
sleep 5

_smoke "Keycloak OIDC discovery"  "http://localhost:30080/realms/org-mind/.well-known/openid-configuration"
_smoke "Prometheus health"         "http://localhost:30909/-/ready"
_smoke "Grafana health"            "http://localhost:30300/api/health"
_smoke "Order Service health"      "http://localhost:30800/health"
_smoke "MCP Server health"         "http://localhost:30808/health"
_smoke "Qdrant health"             "http://localhost:30633/healthz"
_smoke "Skywalking UI"             "http://localhost:30880/" 200

# ── Fetch a demo token ────────────────────────────────────────────────────────
step "Fetching demo Keycloak token"
TOKEN_RESPONSE=$(curl -s \
  -X POST "http://localhost:30080/realms/org-mind/protocol/openid-connect/token" \
  -d "client_id=order-service" \
  -d "client_secret=order-service-secret-2024" \
  -d "username=testuser" \
  -d "password=Test@1234" \
  -d "grant_type=password" \
  --max-time 15 2>/dev/null || echo "{}")

ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4 || true)

if [ -n "$ACCESS_TOKEN" ]; then
  success "Token acquired (truncated): ${ACCESS_TOKEN:0:40}…"

  # Smoke-test the order service with auth
  ORDER_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "http://localhost:30800/orders" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"customer_id":"smoke-test","items":[{"product_id":"p-1","quantity":1,"unit_price":9.99}]}' \
    --max-time 15 2>/dev/null || echo "000")
  if [ "$ORDER_CODE" = "201" ]; then
    success "Order creation via authenticated API: HTTP 201"
  else
    warn "Order creation: expected 201, got ${ORDER_CODE}"
  fi
else
  warn "Could not fetch Keycloak token – Keycloak may still be initialising. Retry manually:"
  echo ""
  echo "  curl -s -X POST http://localhost:30080/realms/org-mind/protocol/openid-connect/token \\"
  echo "    -d 'client_id=order-service' \\"
  echo "    -d 'client_secret=order-service-secret-2024' \\"
  echo "    -d 'username=testuser' \\"
  echo "    -d 'password=Test@1234' \\"
  echo "    -d 'grant_type=password' | python -m json.tool"
fi

# ── Final summary ─────────────────────────────────────────────────────────────
step "Deployment Complete"
echo ""
echo -e "${BOLD}Service URLs${RESET}"
echo -e "  Keycloak Admin    →  ${CYAN}http://localhost:30080/admin${RESET}          admin / admin_pass_2024"
echo -e "  Grafana           →  ${CYAN}http://localhost:30300${RESET}                admin / grafana_pass_2024"
echo -e "  Prometheus        →  ${CYAN}http://localhost:30909${RESET}"
echo -e "  Skywalking UI     →  ${CYAN}http://localhost:30880${RESET}"
echo -e "  Order Service     →  ${CYAN}http://localhost:30800/docs${RESET}           (Bearer token required)"
echo -e "  MCP Server        →  ${CYAN}http://localhost:30808/mcp${RESET}            (Bearer token required)"
echo -e "  Qdrant Dashboard  →  ${CYAN}http://localhost:30633/dashboard${RESET}"
echo ""
echo -e "${BOLD}Claude Desktop / Cursor config${RESET}"
echo '  {
    "mcpServers": {
      "org-mind": {
        "url": "http://localhost:30808/mcp",
        "headers": { "Authorization": "Bearer <your-token>" }
      }
    }
  }'
echo ""
echo -e "${BOLD}Useful commands${RESET}"
echo "  kubectl get pods -n org-mind"
echo "  kubectl logs -f deployment/order-service -n org-mind"
echo "  kubectl logs -f deployment/mcp-server    -n org-mind"
echo "  bash deploy.sh --teardown   # delete everything"
echo ""
success "All done. Happy hacking! 🚀"
