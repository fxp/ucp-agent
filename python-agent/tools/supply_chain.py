"""Supply Chain tools — 库存管理、采购单、每日自动补货。
连接 supply-chain-mock 服务（本地 http://localhost:8091 或通过 SUPPLY_CHAIN_URL 覆盖）。
"""
from __future__ import annotations
import json, os
import httpx
from langchain_core.tools import tool

SC_URL = os.getenv("SUPPLY_CHAIN_URL", "http://localhost:8091")


async def _get(path: str, **params) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{SC_URL}{path}", params={k: v for k, v in params.items() if v is not None})
        return r.json()


async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{SC_URL}{path}", json=body)
        return r.json()


@tool
async def get_inventory(low_stock_only: bool = False) -> str:
    """
    查询贩卖机各货道当前库存数量。
    low_stock_only=True 只返回需要补货的货道（低于最低库存阈值）。
    """
    data = await _get("/inventory", low_stock_only=low_stock_only if low_stock_only else None)
    return json.dumps(data)


@tool
async def check_low_stock() -> str:
    """
    获取当前低库存 / 缺货货道列表，返回需要立即补货的商品信息。
    每日自动补货或运营巡检前调用。
    """
    data = await _get("/inventory", low_stock_only=True)
    return json.dumps(data)


@tool
async def create_purchase_order(
    supplier_id: str,
    sku_ids: list[str],
    quantities: list[int],
    note: str = "",
    priority: str = "normal",
) -> str:
    """
    向供应商提交采购单。priority='urgent' 时直接进入 submitted 状态。
    sku_ids 和 quantities 一一对应，例如 sku_ids=['CC-001','NF-001'], quantities=[24,48]。
    供应商 ID 示例：COKE_CN / NONGFU / MASTER_KONG / NESTLE
    """
    items = [{"sku_id": s, "qty": q} for s, q in zip(sku_ids, quantities)]
    data = await _post("/purchase-orders", {
        "supplier_id": supplier_id, "items": items,
        "note": note, "priority": priority,
    })
    return json.dumps(data)


@tool
async def get_purchase_order_status(po_id: str) -> str:
    """
    查询采购单当前状态和生命周期时间线。
    状态：draft → submitted → acknowledged → shipped → received → stocked
    """
    data = await _get(f"/purchase-orders/{po_id}")
    return json.dumps(data)


@tool
async def preview_daily_order() -> str:
    """
    预览今日自动补货计划（不实际提交）。
    返回系统建议的采购单，基于低库存货道 + 用户预订单汇总。
    运营人员确认后可执行 trigger_daily_order。
    """
    data = await _get("/internal/daily-order/preview")
    return json.dumps(data)


@tool
async def trigger_daily_order(dry_run: bool = False) -> str:
    """
    触发每日自动补货：汇总低库存 + 预订单 → 自动生成采购单提交给供应商。
    dry_run=True 仅预览不实际提交（与 preview_daily_order 等效）。
    """
    data = await _post("/internal/daily-order", {"dry_run": dry_run})
    return json.dumps(data)


@tool
async def list_supply_chain_events(event_type: str = "") -> str:
    """
    查看最近的供应链事件日志（采购单状态变更、库存更新、预订通知等）。
    event_type 可选：po.submitted / po.stocked / preorder.fulfilled / inventory.restocked
    """
    data = await _get("/events", event_type=event_type or None)
    return json.dumps(data)
