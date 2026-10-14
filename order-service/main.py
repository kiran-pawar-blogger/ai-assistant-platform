"""
Order Management Service
========================
FastAPI application that:
  - Validates Keycloak JWTs on protected routes
  - Emits Prometheus metrics (requests, errors, latency, active orders)
  - Sends distributed traces to Apache Skywalking OAP via the sw-python agent
  - Includes intentional /error-demo and /slow-demo routes for telemetry testing
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from enum import Enum
from typing import Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from pydantic import BaseModel, Field
from starlette.responses import Response

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s – %(message)s",
)
logger = logging.getLogger("order-service")

# ── Environment config ────────────────────────────────────────────────────────
KEYCLOAK_URL: str = os.environ.get(
    "KEYCLOAK_URL", "http://keycloak.org-mind.svc.cluster.local:8080"
)
KEYCLOAK_REALM: str = os.environ.get("KEYCLOAK_REALM", "org-mind")
OIDC_JWKS_URI: str = (
    f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
)
OIDC_ISSUER: str = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
TOKEN_AUDIENCE: str = os.environ.get("TOKEN_AUDIENCE", "account")

ERROR_DEMO_ENABLED: bool = os.environ.get("ERROR_DEMO_ENABLED", "true").lower() == "true"
SLOW_DEMO_SECONDS: float = float(os.environ.get("SLOW_DEMO_SECONDS", "3"))

# ── Prometheus metrics ────────────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "order_service_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
ERROR_COUNT = Counter(
    "order_service_errors_total",
    "Total 5xx responses",
    ["endpoint"],
)
REQUEST_LATENCY = Histogram(
    "order_service_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10],
)
ACTIVE_ORDERS = Gauge(
    "order_service_active_orders",
    "Number of non-cancelled orders currently in memory",
)

# ── JWKS cache ────────────────────────────────────────────────────────────────
_jwks_cache: Dict = {}
_jwks_fetched_at: float = 0.0
JWKS_CACHE_TTL: int = 300  # seconds


async def get_jwks() -> Dict:
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
            logger.warning(f"Failed to fetch JWKS: {exc}. Using cached keys.")
    return _jwks_cache


# ── Auth dependency ───────────────────────────────────────────────────────────
async def verify_token(authorization: Optional[str] = Header(None)) -> Dict:
    """Extract and validate a Bearer JWT from the Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1]
    jwks = await get_jwks()

    # Decode without verification first to grab the kid
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token header: {exc}")

    kid = unverified_header.get("kid")
    public_key = None
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            public_key = key
            break

    if public_key is None:
        # Fall back: try with any key (allows dev-mode without Keycloak)
        if not jwks.get("keys"):
            logger.warning("No JWKS keys available – skipping token verification (dev mode)")
            return {"sub": "dev-user", "realm_access": {"roles": ["order-reader", "order-writer"]}}
        raise HTTPException(status_code=401, detail="Public key for token not found in JWKS")

    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=TOKEN_AUDIENCE,
            options={"verify_exp": True, "verify_iss": False},
        )
        return payload
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Token validation failed: {exc}")


def require_role(role: str):
    """Factory for role-checking dependencies."""
    async def _checker(claims: Dict = Depends(verify_token)) -> Dict:
        roles: List[str] = (
            claims.get("realm_access", {}).get("roles", [])
            + claims.get("resource_access", {}).get("order-service", {}).get("roles", [])
        )
        if role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' required",
            )
        return claims
    return _checker


# ── Domain models ─────────────────────────────────────────────────────────────
class OrderStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class OrderItem(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    unit_price: float = Field(gt=0)


class CreateOrderRequest(BaseModel):
    customer_id: str
    items: List[OrderItem]


class Order(BaseModel):
    order_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    customer_id: str
    items: List[OrderItem]
    status: OrderStatus = OrderStatus.PENDING
    created_at: float = Field(default_factory=time.time)
    total: float = 0.0


# ── In-memory store ───────────────────────────────────────────────────────────
_orders: Dict[str, Order] = {}


def _recalculate_active() -> None:
    ACTIVE_ORDERS.set(
        sum(1 for o in _orders.values() if o.status != OrderStatus.CANCELLED)
    )


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Order Service starting up…")
    # Pre-warm JWKS cache
    try:
        await get_jwks()
    except Exception:
        pass
    yield
    logger.info("Order Service shutting down.")


# ── Application ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Order Management Service",
    version="1.0.0",
    description="Enterprise demo order service with Keycloak auth, Prometheus metrics and Skywalking tracing.",
    lifespan=lifespan,
)


# ── Prometheus middleware ─────────────────────────────────────────────────────
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start

    endpoint = request.url.path
    method = request.method
    status_code = str(response.status_code)

    REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code=status_code).inc()
    REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(elapsed)

    if response.status_code >= 500:
        ERROR_COUNT.labels(endpoint=endpoint).inc()

    return response


# ── Metrics endpoint ──────────────────────────────────────────────────────────
@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "service": "order-service"}


# ── Orders CRUD ───────────────────────────────────────────────────────────────
@app.post("/orders", status_code=201, tags=["orders"])
async def create_order(
    body: CreateOrderRequest,
    claims: Dict = Depends(require_role("order-writer")),
):
    """Create a new order. Requires the `order-writer` realm role."""
    order = Order(
        customer_id=body.customer_id,
        items=body.items,
        total=sum(i.quantity * i.unit_price for i in body.items),
    )
    _orders[order.order_id] = order
    _recalculate_active()
    logger.info(f"Created order {order.order_id} for customer {body.customer_id}")
    return order


@app.get("/orders", tags=["orders"])
async def list_orders(
    customer_id: Optional[str] = None,
    _: Dict = Depends(require_role("order-reader")),
):
    """List all orders, optionally filtered by customer_id."""
    results = list(_orders.values())
    if customer_id:
        results = [o for o in results if o.customer_id == customer_id]
    return results


@app.get("/orders/error-demo", tags=["telemetry-demo"])
async def error_demo():
    """
    Intentional error endpoint for telemetry demonstration.
    Adds 2 s of latency then returns HTTP 500.
    Disable via ERROR_DEMO_ENABLED=false env var.
    """
    if not ERROR_DEMO_ENABLED:
        return {"message": "error demo is disabled"}
    await asyncio.sleep(2)
    logger.error("Intentional error triggered on /orders/error-demo")
    raise HTTPException(
        status_code=500,
        detail="Intentional error for telemetry demo – check Grafana & Skywalking",
    )


@app.get("/orders/slow-demo", tags=["telemetry-demo"])
async def slow_demo():
    """
    Intentional slow endpoint. Sleeps SLOW_DEMO_SECONDS (default 3 s).
    Triggers Skywalking slow-trace detection.
    """
    logger.info(f"Slow demo: sleeping {SLOW_DEMO_SECONDS}s")
    await asyncio.sleep(SLOW_DEMO_SECONDS)
    return {"message": f"Slept for {SLOW_DEMO_SECONDS}s – check Skywalking for slow traces"}


@app.get("/orders/{order_id}", tags=["orders"])
async def get_order(
    order_id: str,
    _: Dict = Depends(require_role("order-reader")),
):
    if order_id not in _orders:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return _orders[order_id]


@app.patch("/orders/{order_id}/status", tags=["orders"])
async def update_order_status(
    order_id: str,
    new_status: OrderStatus,
    _: Dict = Depends(require_role("order-writer")),
):
    if order_id not in _orders:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    _orders[order_id].status = new_status
    _recalculate_active()
    return {"order_id": order_id, "new_status": new_status}


@app.delete("/orders/{order_id}", status_code=204, tags=["orders"])
async def cancel_order(
    order_id: str,
    _: Dict = Depends(require_role("order-writer")),
):
    if order_id not in _orders:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    _orders[order_id].status = OrderStatus.CANCELLED
    _recalculate_active()
    logger.info(f"Cancelled order {order_id}")
