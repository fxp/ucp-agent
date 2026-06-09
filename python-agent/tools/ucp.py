"""UCP protocol tools — talk to the stateless mock (or real) UCP server."""
from __future__ import annotations
import json, os, urllib.parse, time, base64
import httpx
from langchain_core.tools import tool
from langgraph.types import interrupt

MOCK_URL = os.getenv("UCP_MOCK_URL", "https://ucp-mock.fxp007.workers.dev")


def _hdr(token: str | None = None) -> dict:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


@tool
async def ucp_discover() -> str:
    """GET /.well-known/ucp — 发现商户能力和支付处理器列表。"""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{MOCK_URL}/.well-known/ucp")
        return r.text


@tool
async def ucp_get_token() -> str:
    """POST /oauth/token — 获取 OAuth 2.0 Bearer Token（client_credentials）。"""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{MOCK_URL}/oauth/token",
            data={"grant_type": "client_credentials", "client_id": "demo", "client_secret": "demo"},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        return r.text


@tool
async def ucp_browse_catalog(token: str) -> str:
    """POST /cart-sessions — 浏览商品目录，返回含 lane_id、价格、库存的列表。"""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{MOCK_URL}/cart-sessions", headers=_hdr(token), json={})
        return r.text


@tool
async def ucp_create_checkout(token: str, lane_id: str, buyer_email: str = "ai-agent@vending.local") -> str:
    """POST /checkout-sessions — 为选定货道商品创建结账会话，返回 checkout_session_id 和价格。"""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{MOCK_URL}/checkout-sessions", headers=_hdr(token), json={
            "line_items": [{"id": lane_id, "quantity": 1}],
            "buyer": {"email": buyer_email},
            "payment": {"handler_id": "alipay_aipay", "instrument": {}},
        })
        return r.text


@tool
async def ucp_request_payment(
    token: str,
    checkout_id: str,
    amount: int,
    currency: str,
    product_name: str,
    lane_id: str,
) -> str:
    """
    向用户请求支付授权。调用此工具后流程暂停，等待用户在前端完成支付确认。
    amount 单位为分（例 500 = ¥5.00）。
    """
    # Fetch alipay / gpay config
    alipay_cfg = None
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            disc = (await c.get(f"{MOCK_URL}/.well-known/ucp")).json()
            for h in disc.get("payment_handlers", []):
                if h["handler_id"] == "alipay_aipay":
                    alipay_cfg = h.get("ap2") or h.get("config")
        except Exception:
            pass

    # Interrupt: pause graph until user confirms payment
    result = interrupt({
        "checkout_id": checkout_id,
        "token": token,
        "amount": amount,
        "currency": currency,
        "product_name": product_name,
        "lane_id": lane_id,
        "alipay": alipay_cfg,
        "requires_user_confirmation": True,
    })

    # result is whatever /api/confirm-payment sends via Command(resume=...)
    # Forward biometric signal so ucp_complete_checkout can embed it in AP2 mandate
    return json.dumps({"payment_authorized": True, **(result or {})})


@tool
async def ucp_complete_checkout(
    token: str,
    checkout_id: str,
    alipay_receipt_id: str = "",
    intent_credential: str = "",
    biometric: dict | None = None,
) -> str:
    """
    服务端验证支付、构建 AP2 mandate（含生物识别信号）并完成结账。返回 order_id 和 pickup_code。
    在 ucp_request_payment 返回后调用，使用支付回调中的 alipay_receipt_id 和 biometric。
    """
    async with httpx.AsyncClient(timeout=20) as c:
        # 1. Server-side Alipay verification (安全隔离原则)
        if alipay_receipt_id:
            vr = await c.get(
                f"{MOCK_URL}/alipay/query-order/{urllib.parse.quote(alipay_receipt_id, safe='')}",
                headers=_hdr(token))
            vdata = vr.json()
            if vdata.get("status") != "paid":
                return json.dumps({"error": f"支付验证失败：{vdata.get('status', 'unknown')}"})
            if vdata.get("checkout_id") and vdata["checkout_id"] != checkout_id:
                return json.dumps({"error": "checkout_id 不匹配，拒绝完成结账。"})
            if vdata.get("intent_credential"):
                intent_credential = vdata["intent_credential"]

        # 2. Build AP2 checkout_mandate (mock SD-JWT, UCP §AP2)
        ap2_mandate = None
        if intent_credential:
            ts = int(time.time())
            verification: dict = {"method": "server_query", "verified_at": ts}
            # Embed biometric signal when available (kiosk mode)
            if biometric and biometric.get("confidence"):
                verification["biometric"] = {
                    "method":       biometric.get("method", "face_recognition"),
                    "provider":     biometric.get("provider", "face-api.js"),
                    "confidence":   biometric.get("confidence"),
                    "voice_verified": biometric.get("voice_verified", False),
                    "verified_at":  biometric.get("verified_at", ts),
                }
            payload = {
                "type": "alipay_intent_credential", "act_version": "ACT/1.0",
                "credential": intent_credential, "checkout_id": checkout_id,
                "issued_by": "ucp-vending-agent-v1", "iat": ts, "exp": ts + 300,
                "verification": verification,
            }
            h = base64.urlsafe_b64encode(
                b'{"alg":"ES256","typ":"ap2+sd-jwt","kid":"ucp-agent-v1"}').decode().rstrip("=")
            p = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
            ap2_mandate = f"{h}.{p}.mock_platform_sig"

        # 3. Complete checkout
        body = {"ap2": {"checkout_mandate": ap2_mandate}} if ap2_mandate else {}
        cr = await c.post(
            f"{MOCK_URL}/checkout-sessions/{urllib.parse.quote(checkout_id, safe='')}/complete",
            headers=_hdr(token), json=body)
        result = cr.json()

        if cr.status_code >= 400:
            msgs = result.get("messages", [])
            msg = msgs[0].get("content") or msgs[0].get("message") if msgs else result.get("error", "未知错误")
            return json.dumps({"error": f"结账失败：{msg}"})

        return json.dumps({
            "order_id": result.get("order_id"),
            "status": result.get("status"),
            "pickup_code": result.get("pickup_code"),
            "pickup_url": result.get("pickup_url"),
            "message": f"结账完成！取货码：{result.get('pickup_code')}，请前往贩卖机扫码取货。",
        })
