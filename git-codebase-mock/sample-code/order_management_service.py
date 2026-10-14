"""
Order Management Service – Sample Source Code
Hosted by git-codebase-mock for RAG ingestion.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel, Field

app = FastAPI(title="Order Management Service", version="1.0.0")


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


class Order(BaseModel):
    order_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    customer_id: str
    items: List[OrderItem]
    status: OrderStatus = OrderStatus.PENDING
    created_at: float = Field(default_factory=time.time)
    total: float = 0.0

    def calculate_total(self) -> float:
        self.total = sum(i.quantity * i.unit_price for i in self.items)
        return self.total


# In-memory store (replace with DB in production)
_orders: dict[str, Order] = {}


@app.post("/orders", response_model=Order, status_code=201)
async def create_order(order: Order, authorization: Optional[str] = Header(None)):
    """Create a new order. Requires a valid Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    order.calculate_total()
    _orders[order.order_id] = order
    return order


@app.get("/orders/{order_id}", response_model=Order)
async def get_order(order_id: str):
    """Retrieve an order by ID."""
    if order_id not in _orders:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return _orders[order_id]


@app.get("/orders", response_model=List[Order])
async def list_orders(customer_id: Optional[str] = None):
    """List all orders, optionally filtered by customer_id."""
    orders = list(_orders.values())
    if customer_id:
        orders = [o for o in orders if o.customer_id == customer_id]
    return orders


@app.patch("/orders/{order_id}/status")
async def update_order_status(order_id: str, status: OrderStatus):
    """Update the status of an existing order."""
    if order_id not in _orders:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    _orders[order_id].status = status
    return {"order_id": order_id, "new_status": status}


@app.delete("/orders/{order_id}", status_code=204)
async def cancel_order(order_id: str):
    """Cancel (soft-delete) an order."""
    if order_id not in _orders:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    _orders[order_id].status = OrderStatus.CANCELLED
