"""
Supply Chain Mock Server — 供应链管理 API Mock
用于与合作伙伴讨论系统集成方案。

Run:
    pip install fastapi uvicorn
    python server.py       # → http://localhost:8091
    # OpenAPI docs: http://localhost:8091/docs

Lifecycle:
    Preorder (用户预订)
      └─► Daily Auto-Order (每日自动汇总)
            └─► Purchase Order: draft → submitted → acknowledged → shipped → received
                  └─► Inventory Restock (入库/上架)
                        └─► Preorder Fulfilled + User Notified
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, HTTPException, Path, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("supply-chain-mock")

app = FastAPI(
    title="Supply Chain Mock API",
    description=(
        "自动贩卖机供应链管理 API — 合作伙伴讨论版本\n\n"
        "覆盖：供应商目录 / 采购单生命周期 / 库存管理 / 用户预订单 / 每日自动补货"
    ),
    version="0.1.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Enums ─────────────────────────────────────────────────────────────────────

class POStatus(str, Enum):
    DRAFT       = "draft"          # 草稿
    SUBMITTED   = "submitted"      # 已提交给供应商
    ACKNOWLEDGED = "acknowledged"  # 供应商已确认
    SHIPPED     = "shipped"        # 已发货
    RECEIVED    = "received"       # 已收货（仓库）
    STOCKED     = "stocked"        # 已上架（入机器）

class PreorderStatus(str, Enum):
    PENDING_STOCK = "pending_stock"  # 等待补货
    STOCK_ARRIVED = "stock_arrived"  # 货已到仓
    FULFILLED     = "fulfilled"      # 已取货 / 已通知
    CANCELLED     = "cancelled"      # 取消


# ── Seed Data ─────────────────────────────────────────────────────────────────

_SUPPLIERS: dict[str, dict] = {
    "COKE_CN": {
        "id": "COKE_CN", "name": "可口可乐（中国）饮料有限公司",
        "contact": "orders@coca-cola.cn", "lead_time_days": 2, "min_order_yuan": 500,
        "payment_terms": "月结30天",
    },
    "NONGFU": {
        "id": "NONGFU", "name": "农夫山泉股份有限公司",
        "contact": "b2b@nongfu.com", "lead_time_days": 1, "min_order_yuan": 300,
        "payment_terms": "款到发货",
    },
    "MASTER_KONG": {
        "id": "MASTER_KONG", "name": "顶新国际集团（康师傅）",
        "contact": "supply@masterkong.com.cn", "lead_time_days": 2, "min_order_yuan": 600,
        "payment_terms": "月结60天",
    },
    "NESTLE": {
        "id": "NESTLE", "name": "雀巢（中国）有限公司",
        "contact": "nestlepro@nestle.com.cn", "lead_time_days": 3, "min_order_yuan": 800,
        "payment_terms": "月结30天",
    },
}

_SKUS: dict[str, list[dict]] = {
    "COKE_CN": [
        {"sku_id": "CC-001", "name": "无糖可乐 330ml",       "brand": "Coca-Cola", "cost_cents": 180, "retail_cents": 350, "moq": 24, "unit": "罐", "weight_g": 330},
        {"sku_id": "CC-002", "name": "雪碧无糖 330ml",       "brand": "Sprite",    "cost_cents": 180, "retail_cents": 350, "moq": 24, "unit": "罐", "weight_g": 330},
        {"sku_id": "CC-003", "name": "零卡可乐 500ml PET",   "brand": "Coca-Cola", "cost_cents": 220, "retail_cents": 450, "moq": 12, "unit": "瓶", "weight_g": 500},
    ],
    "NONGFU": [
        {"sku_id": "NF-001", "name": "矿泉水 550ml",         "brand": "农夫山泉", "cost_cents":  90, "retail_cents": 200, "moq": 48, "unit": "瓶", "weight_g": 550},
        {"sku_id": "NF-002", "name": "东方树叶绿茶 500ml",   "brand": "东方树叶", "cost_cents": 220, "retail_cents": 500, "moq": 12, "unit": "瓶", "weight_g": 500},
        {"sku_id": "NF-003", "name": "苏打气泡水 330ml",     "brand": "农夫山泉", "cost_cents": 200, "retail_cents": 400, "moq": 24, "unit": "罐", "weight_g": 330},
        {"sku_id": "NF-004", "name": "17.5° NFC 橙汁 900ml","brand": "农夫山泉", "cost_cents": 950, "retail_cents":1800, "moq":  6, "unit": "瓶", "weight_g": 900},
    ],
    "MASTER_KONG": [
        {"sku_id": "MK-001", "name": "冰红茶 500ml",         "brand": "康师傅", "cost_cents": 160, "retail_cents": 350, "moq": 24, "unit": "瓶", "weight_g": 500},
        {"sku_id": "MK-002", "name": "茉莉蜜茶 500ml",       "brand": "康师傅", "cost_cents": 160, "retail_cents": 350, "moq": 24, "unit": "瓶", "weight_g": 500},
        {"sku_id": "MK-003", "name": "矿物质水 550ml",       "brand": "康师傅", "cost_cents":  80, "retail_cents": 200, "moq": 48, "unit": "瓶", "weight_g": 550},
    ],
    "NESTLE": [
        {"sku_id": "NE-001", "name": "雀巢拿铁咖啡 268ml",  "brand": "雀巢",  "cost_cents": 600, "retail_cents":1200, "moq": 12, "unit": "罐", "weight_g": 268},
        {"sku_id": "NE-002", "name": "雀巢美式黑咖啡 268ml","brand": "雀巢",  "cost_cents": 600, "retail_cents":1200, "moq": 12, "unit": "罐", "weight_g": 268},
        {"sku_id": "NE-003", "name": "特趣牛奶巧克力 40g",  "brand": "雀巢",  "cost_cents": 380, "retail_cents": 800, "moq":  6, "unit": "根", "weight_g":  40},
    ],
}

# lane_id → inventory entry
_INVENTORY: dict[str, dict] = {
    "lane-001": {"lane_id": "lane-001", "sku_id": "CC-001", "name": "无糖可乐 330ml",      "qty": 8,  "min_qty": 5, "capacity": 15, "location": "A1"},
    "lane-002": {"lane_id": "lane-002", "sku_id": "NF-001", "name": "矿泉水 550ml",        "qty": 12, "min_qty": 8, "capacity": 20, "location": "A2"},
    "lane-003": {"lane_id": "lane-003", "sku_id": "MK-001", "name": "冰红茶 500ml",        "qty": 0,  "min_qty": 5, "capacity": 15, "location": "A3"},
    "lane-004": {"lane_id": "lane-004", "sku_id": "NF-002", "name": "东方树叶绿茶 500ml",  "qty": 3,  "min_qty": 4, "capacity": 12, "location": "B1"},
    "lane-005": {"lane_id": "lane-005", "sku_id": "NE-001", "name": "雀巢拿铁咖啡 268ml", "qty": 6,  "min_qty": 3, "capacity": 10, "location": "B2"},
    "lane-006": {"lane_id": "lane-006", "sku_id": "NF-003", "name": "苏打气泡水 330ml",    "qty": 0,  "min_qty": 5, "capacity": 15, "location": "B3"},
    "lane-007": {"lane_id": "lane-007", "sku_id": "CC-002", "name": "雪碧无糖 330ml",      "qty": 9,  "min_qty": 5, "capacity": 15, "location": "C1"},
    "lane-008": {"lane_id": "lane-008", "sku_id": "MK-002", "name": "茉莉蜜茶 500ml",      "qty": 2,  "min_qty": 5, "capacity": 15, "location": "C2"},
}

# Runtime state
_purchase_orders: dict[str, dict] = {}
_preorders: dict[str, dict] = {}
_events: list[dict] = []
_webhooks: list[dict] = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"

def _po_id() -> str:
    return f"PO-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

def _pre_id() -> str:
    return f"PRE-{uuid.uuid4().hex[:8].upper()}"

def _emit(event_type: str, data: dict) -> None:
    entry = {"event_id": uuid.uuid4().hex[:8], "type": event_type, "data": data, "occurred_at": _now()}
    _events.append(entry)
    log.info("[event] %s %s", event_type, data)
    # Fire webhooks in background (stub: just log)
    for wh in _webhooks:
        if event_type in wh.get("events", []):
            log.info("[webhook] → %s : %s", wh["url"], event_type)

PO_NEXT: dict[POStatus, POStatus] = {
    POStatus.DRAFT:        POStatus.SUBMITTED,
    POStatus.SUBMITTED:    POStatus.ACKNOWLEDGED,
    POStatus.ACKNOWLEDGED: POStatus.SHIPPED,
    POStatus.SHIPPED:      POStatus.RECEIVED,
    POStatus.RECEIVED:     POStatus.STOCKED,
}


# ── Pydantic Models ────────────────────────────────────────────────────────────

class POItem(BaseModel):
    sku_id: str
    qty: int = Field(ge=1)

class CreatePORequest(BaseModel):
    supplier_id: str
    items: list[POItem]
    note: str = ""
    priority: str = "normal"  # normal | urgent

class AdvancePORequest(BaseModel):
    to_status: POStatus | None = None  # None → next in lifecycle

class CreatePreorderRequest(BaseModel):
    sku_id: str
    sku_name: str
    qty: int = Field(default=1, ge=1)
    user_id: str
    note: str = ""
    notify_channel: str = "slack"  # slack | wechat | feishu | email

class RestockItem(BaseModel):
    lane_id: str
    sku_id: str
    qty: int = Field(ge=1)

class RestockRequest(BaseModel):
    po_id: str = ""
    items: list[RestockItem]
    note: str = ""

class WebhookSubscription(BaseModel):
    url: str
    events: list[str] = Field(default=[
        "po.submitted", "po.shipped", "po.received", "po.stocked",
        "preorder.fulfilled", "inventory.low_stock",
    ])
    secret: str = ""


# ── Suppliers ──────────────────────────────────────────────────────────────────

@app.get("/suppliers", tags=["Suppliers"], summary="供应商列表")
async def list_suppliers():
    """返回所有合作供应商，含联系方式和结算条件。"""
    return {"suppliers": list(_SUPPLIERS.values()), "total": len(_SUPPLIERS)}


@app.get("/suppliers/{supplier_id}", tags=["Suppliers"], summary="供应商详情")
async def get_supplier(supplier_id: str):
    """获取单个供应商详情。"""
    s = _SUPPLIERS.get(supplier_id)
    if not s:
        raise HTTPException(404, f"Supplier {supplier_id} not found")
    return s


@app.get("/suppliers/{supplier_id}/skus", tags=["Suppliers"], summary="供应商 SKU 目录")
async def list_supplier_skus(
    supplier_id: str,
    keyword: str = Query(default="", description="商品名关键词"),
):
    """
    获取供应商 SKU 商品目录，含进货价（cost_cents）和建议零售价（retail_cents）。
    keyword 可按名称模糊过滤。
    """
    skus = _SKUS.get(supplier_id)
    if skus is None:
        raise HTTPException(404, f"Supplier {supplier_id} not found")
    if keyword:
        skus = [s for s in skus if keyword.lower() in s["name"].lower() or keyword in s.get("brand", "")]
    return {"supplier_id": supplier_id, "skus": skus, "total": len(skus)}


# ── Purchase Orders ────────────────────────────────────────────────────────────

@app.post("/purchase-orders", tags=["Purchase Orders"], status_code=201, summary="创建采购单")
async def create_purchase_order(req: CreatePORequest):
    """
    向指定供应商提交采购单。
    - 状态从 `draft` 开始（如需立即提交设 `priority=urgent`）
    - 实际系统会通过 EDI/API 自动发送给供应商
    """
    supplier = _SUPPLIERS.get(req.supplier_id)
    if not supplier:
        raise HTTPException(404, f"Supplier {req.supplier_id} not found")

    # Enrich items with SKU details
    all_skus = {s["sku_id"]: s for s in _SKUS.get(req.supplier_id, [])}
    enriched = []
    total_cost = 0
    for item in req.items:
        sku = all_skus.get(item.sku_id)
        if not sku:
            raise HTTPException(400, f"SKU {item.sku_id} not found under supplier {req.supplier_id}")
        subtotal = sku["cost_cents"] * item.qty
        total_cost += subtotal
        enriched.append({**item.model_dump(), "name": sku["name"], "cost_cents": sku["cost_cents"],
                         "subtotal_cents": subtotal})

    initial_status = POStatus.SUBMITTED if req.priority == "urgent" else POStatus.DRAFT
    po = {
        "po_id":       _po_id(),
        "supplier_id": req.supplier_id,
        "supplier_name": supplier["name"],
        "status":      initial_status,
        "items":       enriched,
        "total_cost_cents": total_cost,
        "note":        req.note,
        "priority":    req.priority,
        "eta":         (datetime.utcnow() + timedelta(days=supplier["lead_time_days"])).strftime("%Y-%m-%d"),
        "created_at":  _now(),
        "timeline":    [{"status": initial_status, "at": _now(), "note": "采购单创建"}],
    }
    _purchase_orders[po["po_id"]] = po
    _emit("po.created", {"po_id": po["po_id"], "supplier_id": req.supplier_id,
                          "total_cost_cents": total_cost, "status": initial_status})
    return po


@app.get("/purchase-orders", tags=["Purchase Orders"], summary="采购单列表")
async def list_purchase_orders(
    status: POStatus | None = Query(default=None, description="按状态筛选"),
    supplier_id: str = Query(default="", description="按供应商筛选"),
):
    """列出所有采购单，支持按状态和供应商过滤。"""
    pos = list(_purchase_orders.values())
    if status:
        pos = [p for p in pos if p["status"] == status]
    if supplier_id:
        pos = [p for p in pos if p["supplier_id"] == supplier_id]
    pos.sort(key=lambda p: p["created_at"], reverse=True)
    return {"purchase_orders": pos, "total": len(pos)}


@app.get("/purchase-orders/{po_id}", tags=["Purchase Orders"], summary="采购单详情")
async def get_purchase_order(po_id: str):
    """获取采购单详情，含完整生命周期时间线。"""
    po = _purchase_orders.get(po_id)
    if not po:
        raise HTTPException(404, f"PO {po_id} not found")
    return po


@app.post("/purchase-orders/{po_id}/advance", tags=["Purchase Orders"], summary="推进采购单状态（演示用）")
async def advance_purchase_order(po_id: str, req: AdvancePORequest = Body(default=AdvancePORequest())):
    """
    **演示/测试专用** — 手动推进采购单到下一状态（或指定状态）。
    生产环境此接口由供应商系统 / 物流系统 Webhook 触发。

    完整流程：draft → submitted → acknowledged → shipped → received → stocked
    当状态变为 `stocked` 时，自动触发关联预订单的履行通知。
    """
    po = _purchase_orders.get(po_id)
    if not po:
        raise HTTPException(404, f"PO {po_id} not found")

    current = POStatus(po["status"])
    if current == POStatus.STOCKED:
        raise HTTPException(400, "PO already fully stocked")

    next_status = req.to_status or PO_NEXT.get(current)
    if not next_status:
        raise HTTPException(400, f"Cannot advance from {current}")

    po["status"] = next_status
    po["timeline"].append({"status": next_status, "at": _now()})
    _emit(f"po.{next_status}", {"po_id": po_id, "status": next_status})

    # When received → auto-update inventory and fulfill preorders
    if next_status == POStatus.STOCKED:
        await _auto_restock_from_po(po)

    return po


async def _auto_restock_from_po(po: dict) -> None:
    """When a PO reaches STOCKED, find matching lanes and restock."""
    restocked = []
    for item in po["items"]:
        for lane in _INVENTORY.values():
            if lane["sku_id"] == item["sku_id"] and lane["qty"] < lane["capacity"]:
                add = min(item["qty"], lane["capacity"] - lane["qty"])
                lane["qty"] += add
                lane.setdefault("last_restocked_at", _now())
                lane["last_restocked_at"] = _now()
                restocked.append({"lane_id": lane["lane_id"], "sku_id": item["sku_id"], "qty_added": add})
                break

    if restocked:
        _emit("inventory.restocked", {"po_id": po["po_id"], "lanes": restocked})

    # Fulfill pending preorders for restocked SKUs
    restocked_skus = {r["sku_id"] for r in restocked}
    for pre in _preorders.values():
        if pre["sku_id"] in restocked_skus and pre["status"] == PreorderStatus.PENDING_STOCK:
            pre["status"] = PreorderStatus.STOCK_ARRIVED
            pre["stock_arrived_at"] = _now()
            _emit("preorder.stock_arrived", {"preorder_id": pre["preorder_id"],
                                              "sku_id": pre["sku_id"], "user_id": pre["user_id"]})
            log.info("[notify] user %s → %s arrived, channel=%s",
                     pre["user_id"], pre["sku_name"], pre["notify_channel"])


# ── Inventory ─────────────────────────────────────────────────────────────────

@app.get("/inventory", tags=["Inventory"], summary="库存列表")
async def list_inventory(low_stock_only: bool = Query(default=False)):
    """
    获取所有货道当前库存。
    `low_stock_only=true` 只返回库存低于 min_qty 的货道（需补货）。
    """
    lanes = list(_INVENTORY.values())
    if low_stock_only:
        lanes = [l for l in lanes if l["qty"] < l["min_qty"]]
    return {
        "inventory": lanes,
        "total_lanes": len(lanes),
        "low_stock_count": sum(1 for l in _INVENTORY.values() if l["qty"] < l["min_qty"]),
        "out_of_stock_count": sum(1 for l in _INVENTORY.values() if l["qty"] == 0),
    }


@app.get("/inventory/{lane_id}", tags=["Inventory"], summary="单货道库存")
async def get_lane_inventory(lane_id: str):
    lane = _INVENTORY.get(lane_id)
    if not lane:
        raise HTTPException(404, f"Lane {lane_id} not found")
    return lane


@app.post("/inventory/restock", tags=["Inventory"], summary="手动入库上架")
async def restock_inventory(req: RestockRequest):
    """
    手动将商品入库（调整库存数量）。
    通常由 PO advance to `stocked` 自动触发，也可手动调整。
    """
    updated = []
    for item in req.items:
        lane = _INVENTORY.get(item.lane_id)
        if not lane:
            raise HTTPException(404, f"Lane {item.lane_id} not found")
        if lane["sku_id"] != item.sku_id:
            raise HTTPException(400, f"Lane {item.lane_id} holds {lane['sku_id']}, not {item.sku_id}")
        added = min(item.qty, lane["capacity"] - lane["qty"])
        lane["qty"] += added
        lane["last_restocked_at"] = _now()
        updated.append({"lane_id": item.lane_id, "sku_id": item.sku_id,
                        "qty_added": added, "new_qty": lane["qty"]})

    _emit("inventory.restocked", {"po_id": req.po_id or "manual", "lanes": updated})
    return {"ok": True, "updated": updated}


# ── Preorders ─────────────────────────────────────────────────────────────────

@app.get("/preorders", tags=["Preorders"], summary="预订单列表")
async def list_preorders(
    status: PreorderStatus | None = Query(default=None),
    user_id: str = Query(default=""),
):
    """列出用户预订单（货柜缺货时创建）。"""
    orders = list(_preorders.values())
    if status:
        orders = [o for o in orders if o["status"] == status]
    if user_id:
        orders = [o for o in orders if o["user_id"] == user_id]
    orders.sort(key=lambda o: o["created_at"], reverse=True)
    return {"preorders": orders, "total": len(orders)}


@app.post("/preorders", tags=["Preorders"], status_code=201, summary="创建预订单")
async def create_preorder(req: CreatePreorderRequest):
    """
    用户对缺货商品发起预订。
    创建后自动关联到待补货队列，PO 到货后系统触发 `preorder.stock_arrived` 事件并通知用户。
    """
    pre = {
        "preorder_id":    _pre_id(),
        "sku_id":         req.sku_id,
        "sku_name":       req.sku_name,
        "qty":            req.qty,
        "user_id":        req.user_id,
        "note":           req.note,
        "notify_channel": req.notify_channel,
        "status":         PreorderStatus.PENDING_STOCK,
        "created_at":     _now(),
    }
    _preorders[pre["preorder_id"]] = pre
    _emit("preorder.created", {"preorder_id": pre["preorder_id"], "sku_id": req.sku_id,
                                "user_id": req.user_id})
    return pre


@app.post("/preorders/{preorder_id}/notify", tags=["Preorders"], summary="手动触发到货通知（演示）")
async def notify_preorder_fulfilled(preorder_id: str):
    """
    **演示用** — 标记预订单货到并模拟发送通知给用户。
    生产环境由 PO stocked 事件自动触发。
    """
    pre = _preorders.get(preorder_id)
    if not pre:
        raise HTTPException(404, f"Preorder {preorder_id} not found")
    pre["status"] = PreorderStatus.FULFILLED
    pre["fulfilled_at"] = _now()
    _emit("preorder.fulfilled", {"preorder_id": preorder_id, "user_id": pre["user_id"],
                                  "sku_name": pre["sku_name"], "channel": pre["notify_channel"]})
    return {
        "ok": True,
        "preorder_id": preorder_id,
        "notification_sent": {
            "user_id": pre["user_id"],
            "channel": pre["notify_channel"],
            "message": f"您预订的「{pre['sku_name']}」已到货，可前往贩卖机取货",
        },
    }


# ── Daily Auto-Order ──────────────────────────────────────────────────────────

@app.get("/internal/daily-order/preview", tags=["Automation"], summary="预览今日自动补货计划")
async def preview_daily_order():
    """
    预览系统今日会自动提交哪些采购单（不实际提交）。
    规则：低库存货道 + 未满足的用户预订单 → 汇总为 PO。
    """
    return await _build_daily_order(dry_run=True)


@app.post("/internal/daily-order", tags=["Automation"], summary="触发每日自动补货")
async def run_daily_order(dry_run: bool = Body(default=False, embed=True)):
    """
    执行每日自动补货逻辑。生产环境由 cron 定时触发（建议每天 08:00）。
    - 汇总低库存货道需求
    - 汇总待补货预订单
    - 按供应商分组生成采购单
    - `dry_run=true` 只预览不提交
    """
    return await _build_daily_order(dry_run=dry_run)


async def _build_daily_order(dry_run: bool) -> dict:
    # Gather demand: low stock lanes
    low_lanes = [l for l in _INVENTORY.values() if l["qty"] < l["min_qty"]]

    # Map sku_id → supplier_id
    sku_to_supplier: dict[str, str] = {}
    for sup_id, skus in _SKUS.items():
        for sku in skus:
            sku_to_supplier[sku["sku_id"]] = sup_id

    # Group orders by supplier
    supplier_orders: dict[str, dict[str, int]] = {}  # supplier → {sku_id → qty}

    for lane in low_lanes:
        sku_id = lane["sku_id"]
        sup_id = sku_to_supplier.get(sku_id)
        if not sup_id:
            continue
        need = lane["capacity"] - lane["qty"]  # fill to capacity
        supplier_orders.setdefault(sup_id, {})[sku_id] = \
            supplier_orders.get(sup_id, {}).get(sku_id, 0) + need

    # Add pending preorders
    for pre in _preorders.values():
        if pre["status"] == PreorderStatus.PENDING_STOCK:
            sku_id = pre["sku_id"]
            sup_id = sku_to_supplier.get(sku_id)
            if sup_id:
                supplier_orders.setdefault(sup_id, {})[sku_id] = \
                    supplier_orders.get(sup_id, {}).get(sku_id, 0) + pre["qty"]

    # Build plan
    plan = []
    for sup_id, sku_qtys in supplier_orders.items():
        items = [{"sku_id": k, "qty": v} for k, v in sku_qtys.items()]
        plan.append({"supplier_id": sup_id, "supplier_name": _SUPPLIERS[sup_id]["name"],
                     "items": items, "submitted": False})

    created_pos = []
    if not dry_run:
        for entry in plan:
            req = CreatePORequest(supplier_id=entry["supplier_id"],
                                  items=[POItem(**i) for i in entry["items"]],
                                  note="每日自动补货")
            po = await create_purchase_order(req)
            entry["po_id"] = po["po_id"]
            entry["submitted"] = True
            created_pos.append(po["po_id"])

    return {
        "dry_run": dry_run,
        "plan": plan,
        "low_stock_lanes": len(low_lanes),
        "pending_preorders": sum(1 for p in _preorders.values() if p["status"] == PreorderStatus.PENDING_STOCK),
        "pos_created": created_pos,
        "generated_at": _now(),
    }


# ── Events & Webhooks ─────────────────────────────────────────────────────────

@app.get("/events", tags=["Observability"], summary="事件日志")
async def list_events(
    limit: int = Query(default=50, le=200),
    event_type: str = Query(default=""),
):
    """
    返回最近的系统事件（供应链状态变更、库存调整、预订通知等）。
    生产环境可对接 Kafka / RabbitMQ。
    """
    evs = list(reversed(_events))
    if event_type:
        evs = [e for e in evs if event_type in e["type"]]
    return {"events": evs[:limit], "total": len(evs)}


@app.post("/webhooks", tags=["Observability"], status_code=201, summary="订阅 Webhook")
async def subscribe_webhook(req: WebhookSubscription):
    """
    订阅供应链事件 Webhook（生产环境实现）。
    支持事件：po.submitted / po.shipped / po.received / po.stocked /
              preorder.fulfilled / inventory.low_stock / inventory.restocked
    """
    sub = {"id": uuid.uuid4().hex[:8], **req.model_dump(), "created_at": _now()}
    _webhooks.append(sub)
    return {"ok": True, "subscription": sub}


@app.get("/webhooks", tags=["Observability"], summary="查看 Webhook 订阅")
async def list_webhooks():
    return {"webhooks": _webhooks}


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Meta"])
async def health():
    return {
        "ok": True,
        "inventory_lanes": len(_INVENTORY),
        "purchase_orders": len(_purchase_orders),
        "preorders": len(_preorders),
        "events": len(_events),
    }


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8091, reload=True)
