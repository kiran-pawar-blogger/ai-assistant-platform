"""
Org-Mind MCP Server
====================
FastMCP 2.x server exposing three tools over Streamable HTTP.

Tools:
  1. search_codebase       - Hybrid RAG search over code + docs via Qdrant
  2. get_cluster_telemetry - Live metrics from Prometheus + Skywalking traces
  3. validate_iam_access   - Decode and inspect a Keycloak JWT

The server runs FastMCP's built-in HTTP transport on port 8080.
A separate lightweight HTTP server on port 8081 serves /health and /metrics
so Kubernetes probes and Prometheus can reach them without going through MCP.

Authentication: requests to the MCP endpoint are validated against Keycloak
JWKS inside each tool call. Unauthenticated calls get an error result, not a
hard 401, because FastMCP 2.x does not expose request-level middleware hooks
at the tool-dispatch layer; full gateway-level auth is left to a sidecar/ingress
in a production setup.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import httpx
from fastmcp import FastMCP
from jose import JWTError, jwt
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# â”€â”€ Logging â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("mcp-server")

# â”€â”€ Environment â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
KEYCLOAK_URL: str = os.environ.get(
    "KEYCLOAK_URL", "http://keycloak.org-mind.svc.cluster.local:8080"
)
KEYCLOAK_REALM: str = os.environ.get("KEYCLOAK_REALM", "org-mind")
OIDC_JWKS_URI: str = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
OIDC_ISSUER: str = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
TOKEN_AUDIENCE: str = os.environ.get("TOKEN_AUDIENCE", "account")

QDRANT_URL: str = os.environ.get("QDRANT_URL", "http://qdrant.org-mind.svc.cluster.local:6333")
PROMETHEUS_URL: str = os.environ.get("PROMETHEUS_URL", "http://prometheus.org-mind.svc.cluster.local:9090")
SKYWALKING_URL: str = os.environ.get("SKYWALKING_OAP_URL", "http://skywalking-oap.org-mind.svc.cluster.local:12800")
GIT_MOCK_URL: str = os.environ.get("GIT_MOCK_URL", "http://git-codebase-mock.org-mind.svc.cluster.local:8080")
DOC_SITE_URL: str = os.environ.get("DOC_SITE_URL", "http://doc-site.org-mind.svc.cluster.local:80")

QDRANT_COLLECTION: str = "org-mind-codebase"
EMBEDDING_DIM: int = 384

# â”€â”€ Prometheus metrics â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
TOOL_CALLS = Counter("mcp_tool_calls_total", "Tool invocations", ["tool"])
TOOL_ERRORS = Counter("mcp_tool_errors_total", "Tool errors", ["tool"])
TOOL_LATENCY = Histogram(
    "mcp_tool_duration_seconds", "Tool latency in seconds", ["tool"],
    buckets=[0.05, 0.1, 0.5, 1, 2, 5, 10],
)

# â”€â”€ JWKS cache â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_jwks_cache: dict = {}
_jwks_fetched_at: float = 0.0
JWKS_CACHE_TTL: int = 300


async def _fetch_jwks() -> dict:
    global _jwks_cache, _jwks_fetched_at
    now = time.time()
    if _jwks_cache and (now - _jwks_fetched_at) < JWKS_CACHE_TTL:
        return _jwks_cache
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(OIDC_JWKS_URI)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_fetched_at = now
            logger.info("JWKS refreshed from Keycloak")
        except Exception as exc:
            logger.warning(f"JWKS refresh failed: {exc}")
    return _jwks_cache


async def _validate_token(token: str) -> dict:
    """Validate a raw JWT string. Returns decoded claims or raises ValueError."""
    jwks = await _fetch_jwks()
    if not jwks.get("keys"):
        logger.warning("No JWKS keys - running in dev mode without token verification")
        return {"sub": "dev-user", "realm_access": {"roles": ["admin"]}}
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise ValueError(f"Bad token header: {exc}")
    kid = header.get("kid")
    key = next((k for k in jwks["keys"] if k.get("kid") == kid), None)
    if key is None:
        raise ValueError("Signing key not found in JWKS")
    try:
        return jwt.decode(token, key, algorithms=["RS256"],
                          audience=TOKEN_AUDIENCE,
                          options={"verify_exp": True, "verify_iss": False})
    except JWTError as exc:
        raise ValueError(f"Token invalid: {exc}")


# â”€â”€ Qdrant helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _ensure_collection(client: httpx.AsyncClient) -> None:
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}"
    resp = await client.get(url)
    if resp.status_code == 200:
        return
    payload = {
        "vectors": {
            "dense": {"size": EMBEDDING_DIM, "distance": "Cosine"},
        }
    }
    await client.put(url, json=payload)
    logger.info(f"Qdrant collection '{QDRANT_COLLECTION}' created")


async def _naive_embed(text: str) -> list[float]:
    """
    Deterministic pseudo-embedding for sandbox use.
    Replace with a real model (e.g. sentence-transformers all-MiniLM-L6-v2)
    for semantic accuracy in production.
    """
    import hashlib
    import random
    seed = int(hashlib.md5(text.encode()).hexdigest(), 16)
    rng = random.Random(seed)
    return [rng.gauss(0, 1) for _ in range(EMBEDDING_DIM)]


async def _ingest_document(client: httpx.AsyncClient, doc_id: str,
                            text: str, source: str, filename: str) -> None:
    vector = await _naive_embed(text)
    point = {
        "id": abs(hash(doc_id)) % (2 ** 63),
        "vector": vector,
        "payload": {
            "text": text[:4000],
            "source": source,
            "filename": filename,
            "doc_id": doc_id,
        },
    }
    await client.put(
        f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points",
        json={"points": [point]},
    )


async def _init_qdrant() -> None:
    """Ingest source code and docs into Qdrant on startup."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            await _ensure_collection(client)
        except Exception as exc:
            logger.warning(f"Qdrant init failed: {exc}")
            return

        for fname in ["order_management_service.py", "pii_encryption_utility.py"]:
            try:
                resp = await client.get(f"{GIT_MOCK_URL}/{fname}")
                if resp.status_code == 200:
                    await _ingest_document(client, f"code:{fname}", resp.text,
                                           "git-codebase-mock", fname)
                    logger.info(f"Ingested: {fname}")
            except Exception as exc:
                logger.warning(f"Could not ingest {fname}: {exc}")

        for page in ["architecture.html", "runbooks.html", "onboarding.html", "api-reference.html"]:
            try:
                resp = await client.get(f"{DOC_SITE_URL}/{page}")
                if resp.status_code == 200:
                    await _ingest_document(client, f"doc:{page}", resp.text,
                                           "doc-site", page)
                    logger.info(f"Ingested: {page}")
            except Exception as exc:
                logger.warning(f"Could not ingest {page}: {exc}")


# â”€â”€ FastMCP server â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
mcp = FastMCP(
    name="org-mind",
    instructions=(
        "Connected to the Org-Mind enterprise sandbox. "
        "Use search_codebase to explore source code and docs, "
        "get_cluster_telemetry to inspect service health, and "
        "validate_iam_access to check token permissions. "
        "Pass your Keycloak Bearer token as the 'token' argument to tools that require auth."
    ),
)


# Custom routes served on the same port 8080 as FastMCP
@mcp.custom_route("/health", methods=["GET"])
async def health_route(request):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok", "service": "mcp-server"})


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics_route(request):
    from starlette.responses import Response as StarletteResponse
    return StarletteResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@mcp.tool()
async def search_codebase(query: str, token: str, top_k: int = 5) -> dict:
    """
    Hybrid RAG search over ingested source code and documentation.

    Args:
        query: Natural-language search query.
        token: Keycloak Bearer JWT (without 'Bearer ' prefix).
        top_k: Number of top results to return (default 5).
    """
    TOOL_CALLS.labels(tool="search_codebase").inc()
    start = time.perf_counter()
    try:
        await _validate_token(token)
    except ValueError as exc:
        TOOL_ERRORS.labels(tool="search_codebase").inc()
        return {"error": f"Unauthorized: {exc}", "results": []}
    try:
        query_vector = await _naive_embed(query)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/search",
                json={
                    "vector": query_vector,
                    "limit": top_k,
                    "with_payload": True,
                    "score_threshold": 0.0,
                },
            )
            if resp.status_code != 200:
                return {"error": f"Qdrant error: {resp.text}", "results": []}
            hits = resp.json().get("result", [])
            return {
                "query": query,
                "results": [
                    {
                        "filename": h["payload"].get("filename"),
                        "source": h["payload"].get("source"),
                        "score": round(h["score"], 4),
                        "excerpt": h["payload"].get("text", "")[:500],
                    }
                    for h in hits
                ],
                "total": len(hits),
            }
    except Exception as exc:
        TOOL_ERRORS.labels(tool="search_codebase").inc()
        return {"error": str(exc), "results": []}
    finally:
        TOOL_LATENCY.labels(tool="search_codebase").observe(time.perf_counter() - start)


@mcp.tool()
async def get_cluster_telemetry(token: str, time_range: str = "5m",
                                 include_traces: bool = True) -> dict:
    """
    Fetch live telemetry for the order-service from Prometheus and Skywalking.

    Args:
        token:          Keycloak Bearer JWT (without 'Bearer ' prefix).
        time_range:     Prometheus range string e.g. '5m', '1h' (default '5m').
        include_traces: Whether to include recent Skywalking traces (default True).
    """
    TOOL_CALLS.labels(tool="get_cluster_telemetry").inc()
    start = time.perf_counter()
    try:
        await _validate_token(token)
    except ValueError as exc:
        TOOL_ERRORS.labels(tool="get_cluster_telemetry").inc()
        return {"error": f"Unauthorized: {exc}"}
    result: dict = {}
    async with httpx.AsyncClient(timeout=15) as client:
        prom_queries = {
            "request_rate_per_sec": f"sum(rate(order_service_requests_total[{time_range}]))",
            "error_rate_per_sec": f"sum(rate(order_service_errors_total[{time_range}]))",
            "p95_latency_sec": (
                f"histogram_quantile(0.95,"
                f"rate(order_service_request_duration_seconds_bucket[{time_range}]))"
            ),
            "active_orders": "order_service_active_orders",
        }
        metrics: dict = {}
        for name, query in prom_queries.items():
            try:
                resp = await client.get(f"{PROMETHEUS_URL}/api/v1/query",
                                        params={"query": query})
                data = resp.json()
                if data.get("status") == "success":
                    res = data["data"]["result"]
                    metrics[name] = res[0]["value"][1] if res else "no data"
                else:
                    metrics[name] = f"error: {data.get('error', 'unknown')}"
            except Exception as exc:
                metrics[name] = f"prometheus unreachable: {exc}"
        result["metrics"] = metrics
        result["time_range"] = time_range
        try:
            err_rate = float(metrics.get("error_rate_per_sec", 0) or 0)
            result["alert"] = (
                "WARNING: ERROR SPIKE DETECTED" if err_rate > 0.05 else "OK: Error rate nominal"
            )
        except (ValueError, TypeError):
            result["alert"] = "Could not evaluate error rate"

        if include_traces:
            try:
                gql = {
                    "query": (
                        "{ queryBasicTraces(condition: {"
                        "  queryDuration: { start: \"now-5\" end: \"now\" step: MINUTE }"
                        "  minTraceDuration: 1000 traceState: ERROR pageNum: 1 pageSize: 10"
                        "}) { traces { endpointNames duration start isError } total } }"
                    )
                }
                sw_resp = await client.post(f"{SKYWALKING_URL}/graphql", json=gql)
                if sw_resp.status_code == 200:
                    sw_data = sw_resp.json()
                    traces = (sw_data.get("data", {})
                              .get("queryBasicTraces", {})
                              .get("traces", []))
                    result["traces"] = {
                        "total_error_traces": (sw_data.get("data", {})
                                               .get("queryBasicTraces", {})
                                               .get("total", 0)),
                        "sample_traces": traces[:5],
                    }
                else:
                    result["traces"] = {"error": f"Skywalking returned {sw_resp.status_code}"}
            except Exception as exc:
                result["traces"] = {"error": f"Skywalking unreachable: {exc}"}
    TOOL_LATENCY.labels(tool="get_cluster_telemetry").observe(time.perf_counter() - start)
    return result


@mcp.tool()
async def validate_iam_access(token: str,
                               required_roles: list[str] | None = None) -> dict:
    """
    Validate a Keycloak JWT and inspect its RBAC roles and scopes.

    Args:
        token:          Raw JWT string (without 'Bearer ' prefix).
        required_roles: Optional list of role names to check.
    """
    TOOL_CALLS.labels(tool="validate_iam_access").inc()
    start = time.perf_counter()
    try:
        payload = await _validate_token(token)
        realm_roles: list[str] = payload.get("realm_access", {}).get("roles", [])
        resource_roles: list[str] = (
            payload.get("resource_access", {})
            .get("order-service", {})
            .get("roles", [])
        )
        all_roles = list(set(realm_roles + resource_roles))
        scopes: list[str] = payload.get("scope", "").split()
        missing = [r for r in (required_roles or []) if r not in all_roles]
        return {
            "valid": True,
            "subject": payload.get("sub"),
            "preferred_username": payload.get("preferred_username"),
            "email": payload.get("email"),
            "roles": all_roles,
            "scopes": scopes,
            "expires_at": payload.get("exp"),
            "issuer": payload.get("iss"),
            "required_roles_check": {
                "required": required_roles or [],
                "missing": missing,
                "access_granted": len(missing) == 0,
            },
        }
    except ValueError as exc:
        TOOL_ERRORS.labels(tool="validate_iam_access").inc()
        return {"valid": False, "error": str(exc)}
    except Exception as exc:
        TOOL_ERRORS.labels(tool="validate_iam_access").inc()
        return {"valid": False, "error": f"Unexpected error: {exc}"}
    finally:
        TOOL_LATENCY.labels(tool="validate_iam_access").observe(time.perf_counter() - start)


# â”€â”€ Side-car HTTP server for /health and /metrics â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class _SidecarHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # suppress access logs

    def do_GET(self):
        if self.path == "/health":
            body = b'{"status":"ok","service":"mcp-server"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/metrics":
            body = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def _start_sidecar(port: int = 8081) -> None:
    server = HTTPServer(("0.0.0.0", port), _SidecarHandler)
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"Sidecar HTTP server running on :{port} (/health, /metrics)")


# â”€â”€ Entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
if __name__ == "__main__":
    async def _startup():
        logger.info("Initialising Qdrant collections...")
        await _init_qdrant()
        logger.info("Qdrant ready.")

    asyncio.run(_startup())
    _start_sidecar(port=8081)

    logger.info("Starting FastMCP server on :8080/mcp ...")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8080, path="/mcp")
