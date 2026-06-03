"""用友宝供应商 API 工具。
凭据未设置时自动降级到 mock 数据。
实际 API 文档：https://open.yonyoucloud.com/doc
"""
from __future__ import annotations
import json, os, uuid
from datetime import datetime
from langchain_core.tools import tool
import httpx

YONYOU_BASE    = os.getenv("YONYOU_BASE_URL",    "https://open.yonyoucloud.com")
YONYOU_APP_ID  = os.getenv("YONYOU_APP_ID",  "")
YONYOU_SECRET  = os.getenv("YONYOU_APP_SECRET", "")
USE_MOCK       = not (YONYOU_APP_ID and YONYOU_SECRET)

# In-memory preorder store (replace with DB in production)
_preorders: list[dict] = []

# Mock supplier catalog
_MOCK_SKU = [
    {"sku_id": "YY-001", "name": "无糖可乐 330ml",   "brand": "可口可乐", "price_cents": 280, "min_order_qty": 24, "eta_days": 2, "unit": "罐"},
    {"sku_id": "YY-002", "name": "无糖绿茶 500ml",   "brand": "康师傅",   "price_cents": 320, "min_order_qty": 12, "eta_days": 2, "unit": "瓶"},
    {"sku_id": "YY-003", "name": "气泡水 330ml",     "brand": "农夫山泉", "price_cents": 380, "min_order_qty": 24, "eta_days": 3, "unit": "罐"},
    {"sku_id": "YY-004", "name": "无糖红牛 250ml",   "brand": "红牛",     "price_cents": 680, "min_order_qty": 12, "eta_days": 3, "unit": "罐"},
    {"sku_id": "YY-005", "name": "低卡百事可乐 330ml","brand": "百事",    "price_cents": 260, "min_order_qty": 24, "eta_days": 2, "unit": "罐"},
    {"sku_id": "YY-006", "name": "矿泉水 550ml",     "brand": "怡宝",     "price_cents": 150, "min_order_qty": 48, "eta_days": 1, "unit": "瓶"},
    {"sku_id": "YY-007", "name": "拿铁咖啡 250ml",   "brand": "雀巢",     "price_cents": 1200,"min_order_qty": 12, "eta_days": 3, "unit": "瓶"},
    {"sku_id": "YY-008", "name": "蛋白质棒 40g",     "brand": "Quest",    "price_cents": 2500,"min_order_qty": 6,  "eta_days": 5, "unit": "根"},
]


async def _yonyou_request(path: str, params: dict) -> dict:
    """调用用友宝开放平台 API（OAuth2 client_credentials）。"""
    # Step 1: get access token
    async with httpx.AsyncClient(timeout=15) as c:
        tok_r = await c.post(f"{YONYOU_BASE}/oauth2/token",
            data={"grant_type": "client_credentials",
                  "client_id": YONYOU_APP_ID,
                  "client_secret": YONYOU_SECRET})
        access_token = tok_r.json().get("access_token", "")
        r = await c.post(f"{YONYOU_BASE}{path}",
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json"},
            json=params)
        return r.json()


@tool
async def query_supplier_sku(keyword: str) -> str:
    """
    查询供应商商品 SKU（用友宝）。货柜缺货时调用，返回可补货商品、进货价和预计到货天数。
    keyword: 商品名称关键词，如"无糖饮料"、"气泡水"。
    """
    if USE_MOCK:
        kw = keyword.lower()
        matches = [
            s for s in _MOCK_SKU
            if any(w in s["name"].lower() or w in s.get("brand", "").lower()
                   for w in kw.split())
        ] or _MOCK_SKU[:3]  # fallback: return first 3
        return json.dumps({
            "source": "mock",
            "note": "设置 YONYOU_APP_ID + YONYOU_APP_SECRET 启用真实用友宝数据",
            "keyword": keyword,
            "results": [
                {**s, "price_yuan": f"¥{s['price_cents']/100:.2f}",
                 "eta": f"{s['eta_days']} 天内到货"}
                for s in matches[:5]
            ],
        })

    # Real 用友宝 API
    data = await _yonyou_request("/yonbip/scm/basic/material/v2/listQuery",
        {"pageNum": 1, "pageSize": 10, "keyword": keyword})
    rows = data.get("data", {}).get("records", [])
    return json.dumps({"source": "yonyou", "keyword": keyword, "results": rows})


@tool
async def create_preorder(
    sku_id: str,
    sku_name: str,
    quantity: int,
    user_id: str,
    note: str = "",
) -> str:
    """
    为用户创建商品预订单（货柜缺货时）。订单进入待补货队列，到货后推送通知。
    """
    order_id = f"PRE-{uuid.uuid4().hex[:8].upper()}"
    preorder = {
        "preorder_id": order_id,
        "sku_id": sku_id,
        "sku_name": sku_name,
        "quantity": quantity,
        "user_id": user_id,
        "note": note,
        "status": "pending_restock",
        "created_at": datetime.now().isoformat(),
    }
    _preorders.append(preorder)

    if not USE_MOCK:
        # Submit to 用友宝 purchase requisition
        try:
            await _yonyou_request("/yonbip/scm/pur/requisition/v2/save", {
                "items": [{"materialCode": sku_id, "qty": quantity, "memo": note}],
                "sourceDocNo": order_id,
            })
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    return json.dumps({
        "ok": True,
        "preorder_id": order_id,
        "message": f"✅ 预订已记录：{sku_name} ×{quantity}，到货后将通知您。",
    })


@tool
async def list_preorders(user_id: str = "") -> str:
    """列出待补货预订单（运营管理用）。"""
    orders = [p for p in _preorders if not user_id or p["user_id"] == user_id]
    return json.dumps({"count": len(orders), "orders": orders})
