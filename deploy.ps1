#Requires -Version 5.1
<#
.SYNOPSIS
    Org-Mind Enterprise AI & Microservices Sandbox - Windows PowerShell bootstrap.

.DESCRIPTION
    Native PowerShell equivalent of deploy.sh. Targets KinD on Windows.
    Run from the project root directory with: .\deploy.ps1

.PARAMETER SkipBuild
    Skip Docker image builds (use when images are already loaded into KinD).

.PARAMETER Teardown
    Delete the KinD cluster and all resources.

.EXAMPLE
    .\deploy.ps1
    .\deploy.ps1 -SkipBuild
    .\deploy.ps1 -Teardown
#>
param(
    [switch]$SkipBuild,
    [switch]$Teardown
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ClusterName = "org-mind"
$Namespace   = "org-mind"
$ScriptDir   = $PSScriptRoot

# --- Helper functions ---------------------------------------------------------
function Write-Info  { param($msg) Write-Host "[INFO]  $msg" -ForegroundColor Cyan    }
function Write-Ok    { param($msg) Write-Host "[OK]    $msg" -ForegroundColor Green   }
function Write-Warn  { param($msg) Write-Host "[WARN]  $msg" -ForegroundColor Yellow  }
function Write-Fail  { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red; exit 1 }
function Write-Step  { param($msg)
    Write-Host ""
    Write-Host ("=" * 52) -ForegroundColor White
    Write-Host "  $msg"   -ForegroundColor White
    Write-Host ("=" * 52) -ForegroundColor White
}

# --- Teardown -----------------------------------------------------------------
if ($Teardown) {
    Write-Step "Tearing down KinD cluster: $ClusterName"
    & kind delete cluster --name $ClusterName
    Write-Ok "Cluster deleted."
    exit 0
}

# --- Prerequisite checks ------------------------------------------------------
Write-Step "Checking prerequisites"
foreach ($cmd in @("docker", "kubectl", "kind")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Fail "'$cmd' not found in PATH. Please install it first."
    }
    Write-Ok "$cmd found"
}

# --- Create or reuse KinD cluster ---------------------------------------------
Write-Step "KinD cluster setup"

# kind get clusters writes "No kind clusters found." to stderr (not an error).
# Capture both streams, keep only string lines, check for our cluster name.
$clusterExists = $false
try {
    $kindOutput = & kind get clusters 2>&1
    $clusterExists = ($kindOutput | ForEach-Object { "$_" }) -contains $ClusterName
} catch {
    $clusterExists = $false
}

if ($clusterExists) {
    Write-Warn "KinD cluster '$ClusterName' already exists - reusing."
} else {
    Write-Info "Creating KinD cluster '$ClusterName' with NodePort mappings..."

    $kindConfig = @"
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30080
        hostPort: 30080
        protocol: TCP
      - containerPort: 30300
        hostPort: 30300
        protocol: TCP
      - containerPort: 30909
        hostPort: 30909
        protocol: TCP
      - containerPort: 30880
        hostPort: 30880
        protocol: TCP
      - containerPort: 30800
        hostPort: 30800
        protocol: TCP
      - containerPort: 30808
        hostPort: 30808
        protocol: TCP
      - containerPort: 30633
        hostPort: 30633
        protocol: TCP
"@

    $tmpConfig = Join-Path $env:TEMP "kind-org-mind.yaml"
    [System.IO.File]::WriteAllText($tmpConfig, $kindConfig, [System.Text.Encoding]::UTF8)

    & kind create cluster --name $ClusterName --config $tmpConfig
    if ($LASTEXITCODE -ne 0) { Write-Fail "KinD cluster creation failed." }

    Remove-Item $tmpConfig -Force
    Write-Ok "KinD cluster created."
}

# --- Build Docker images ------------------------------------------------------
Write-Step "Building Docker images"

if ($SkipBuild) {
    Write-Warn "-SkipBuild specified - skipping image builds."
} else {
    Write-Info "Building order-service image..."
    & docker build -t "org-mind/order-service:latest" `
        -f "$ScriptDir\order-service\Dockerfile" `
        "$ScriptDir\order-service"
    if ($LASTEXITCODE -ne 0) { Write-Fail "order-service Docker build failed." }
    Write-Ok "order-service image built."

    Write-Info "Building mcp-server image..."
    & docker build -t "org-mind/mcp-server:latest" `
        -f "$ScriptDir\mcp-server\Dockerfile" `
        "$ScriptDir\mcp-server"
    if ($LASTEXITCODE -ne 0) { Write-Fail "mcp-server Docker build failed." }
    Write-Ok "mcp-server image built."

    Write-Info "Loading images into KinD..."
    & kind load docker-image "org-mind/order-service:latest" --name $ClusterName
    & kind load docker-image "org-mind/mcp-server:latest"   --name $ClusterName
    Write-Ok "Images loaded into KinD."
}

# --- Apply Kubernetes manifests -----------------------------------------------
Write-Step "Applying Kubernetes manifests"

$manifests = @(
    "manifests\namespace.yaml",
    "manifests\keycloak.yaml",
    "manifests\qdrant.yaml",
    "manifests\simulation.yaml",
    "manifests\observability.yaml",
    "manifests\skywalking.yaml",
    "manifests\order-service.yaml",
    "manifests\mcp-server.yaml"
)

foreach ($m in $manifests) {
    $fullPath = Join-Path $ScriptDir $m
    Write-Info "Applying $m ..."
    & kubectl apply -f $fullPath
    if ($LASTEXITCODE -ne 0) { Write-Fail "kubectl apply failed for $m" }
    Write-Ok "Applied $m"
}

# --- Wait for deployments -----------------------------------------------------
Write-Step "Waiting for deployments to become Ready (up to 5 min each)"

$deployments = @(
    "keycloak-postgres",
    "keycloak",
    "qdrant",
    "git-codebase-mock",
    "doc-site",
    "prometheus",
    "grafana",
    "skywalking-oap",
    "skywalking-ui",
    "order-service",
    "mcp-server"
)

foreach ($d in $deployments) {
    Write-Info "Waiting for deployment/$d ..."
    & kubectl rollout status "deployment/$d" -n $Namespace --timeout=300s 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "$d is Ready."
    } else {
        Write-Warn "$d did not become ready within 5 min."
        Write-Warn "  Check: kubectl describe deployment/$d -n $Namespace"
    }
}

# --- Smoke tests --------------------------------------------------------------
Write-Step "Running smoke tests"
Start-Sleep -Seconds 5

function Test-ServiceUrl {
    param(
        [string]$Label,
        [string]$Url,
        [int]$Expected = 200
    )
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10 -ErrorAction SilentlyContinue
        $code = $resp.StatusCode
    } catch {
        $code = 0
    }
    if ($code -eq $Expected) {
        Write-Ok "${Label}: HTTP $code"
    } else {
        Write-Warn "${Label}: expected HTTP $Expected, got $code  ($Url)"
    }
}

Test-ServiceUrl -Label "Keycloak OIDC"  -Url "http://localhost:30080/realms/org-mind/.well-known/openid-configuration"
Test-ServiceUrl -Label "Prometheus"     -Url "http://localhost:30909/-/ready"
Test-ServiceUrl -Label "Grafana"        -Url "http://localhost:30300/api/health"
Test-ServiceUrl -Label "Order Service"  -Url "http://localhost:30800/health"
Test-ServiceUrl -Label "MCP Server"     -Url "http://localhost:30808/health"
Test-ServiceUrl -Label "Qdrant"         -Url "http://localhost:30633/healthz"
Test-ServiceUrl -Label "Skywalking UI"  -Url "http://localhost:30880/"

# --- Fetch demo token and test auth -------------------------------------------
Write-Step "Fetching demo Keycloak token"

try {
    $tokenBody = @{
        client_id     = "order-service"
        client_secret = "order-service-secret-2024"
        username      = "testuser"
        password      = "Test@1234"
        grant_type    = "password"
    }

    $tokenResp = Invoke-RestMethod `
        -Uri "http://localhost:30080/realms/org-mind/protocol/openid-connect/token" `
        -Method Post `
        -Body $tokenBody `
        -TimeoutSec 15

    $token = $tokenResp.access_token
    $preview = $token.Substring(0, [Math]::Min(40, $token.Length))
    Write-Ok "Token acquired: ${preview}..."

    # Test authenticated order creation
    $orderBody = '{"customer_id":"smoke-test","items":[{"product_id":"p-1","quantity":1,"unit_price":9.99}]}'
    $authHeaders = @{
        Authorization  = "Bearer $token"
        "Content-Type" = "application/json"
    }

    $orderResp = Invoke-WebRequest `
        -Uri "http://localhost:30800/orders" `
        -Method Post `
        -Headers $authHeaders `
        -Body $orderBody `
        -TimeoutSec 15 `
        -UseBasicParsing `
        -ErrorAction SilentlyContinue

    if ($orderResp.StatusCode -eq 201) {
        Write-Ok "Authenticated order creation: HTTP 201"
    } else {
        Write-Warn "Order creation returned HTTP $($orderResp.StatusCode)"
    }

} catch {
    Write-Warn "Could not fetch Keycloak token - Keycloak may still be initialising."
    Write-Host ""
    Write-Host "  Retry manually once all pods are Ready:" -ForegroundColor Yellow
    Write-Host '  $b = @{client_id="order-service";client_secret="order-service-secret-2024";username="testuser";password="Test@1234";grant_type="password"}' -ForegroundColor Gray
    Write-Host '  Invoke-RestMethod -Uri "http://localhost:30080/realms/org-mind/protocol/openid-connect/token" -Method Post -Body $b' -ForegroundColor Gray
}

# --- Summary ------------------------------------------------------------------
Write-Step "Deployment Complete"

Write-Host ""
Write-Host "Service URLs:" -ForegroundColor White
Write-Host "  Keycloak Admin   ->  http://localhost:30080/admin        (admin / admin_pass_2024)"
Write-Host "  Grafana          ->  http://localhost:30300              (admin / grafana_pass_2024)"
Write-Host "  Prometheus       ->  http://localhost:30909"
Write-Host "  Skywalking UI    ->  http://localhost:30880"
Write-Host "  Order Service    ->  http://localhost:30800/docs         (Bearer token required)"
Write-Host "  MCP Server       ->  http://localhost:30808/mcp          (Bearer token required)"
Write-Host "  Qdrant Dashboard ->  http://localhost:30633/dashboard"
Write-Host ""
Write-Host "Claude Desktop config  (%APPDATA%\Claude\claude_desktop_config.json):" -ForegroundColor White
Write-Host '  {'
Write-Host '    "mcpServers": {'
Write-Host '      "org-mind": {'
Write-Host '        "url": "http://localhost:30808/mcp",'
Write-Host '        "headers": { "Authorization": "Bearer <your-token>" }'
Write-Host '      }'
Write-Host '    }'
Write-Host '  }'
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor White
Write-Host "  kubectl get pods -n org-mind"
Write-Host "  kubectl logs -f deployment/order-service -n org-mind"
Write-Host "  kubectl logs -f deployment/mcp-server    -n org-mind"
Write-Host "  .\deploy.ps1 -Teardown"
Write-Host ""
Write-Ok "All done. Happy hacking!"
