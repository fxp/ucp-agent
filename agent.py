"""
UCP Customer Agent
==================
GLM-4-powered agent implementing the UCP Platform (buyer) role.

Payment flow:
  1. Agent discovers merchant + payment handlers
  2. Agent creates checkout session (gets price)
  3. Agent calls request_payment_confirmation → stream pauses, UI shows payment dialog
  4. User reviews amount + item, clicks "Confirm Payment"
  5. /api/confirm-payment completes the checkout + tracks order

Run:
    pip install fastapi uvicorn openai httpx
    python agent.py
    open http://localhost:8090
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any, AsyncGenerator

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

# ── Config ─────────────────────────────────────────────────────────────────────

PORT         = int(os.environ.get("AGENT_PORT", 8090))
MERCHANT_URL = os.environ.get("MERCHANT_URL", "http://localhost:8080")
GLM_API_KEY  = os.environ.get("GLM_API_KEY", "1790d449b46d437bbc8b101815048d64.lEMGH4tememSXbvH")
GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
GLM_MODEL    = "glm-4-flash"
AGENT_BASE   = f"http://localhost:{PORT}"

# ── Agent Profile ───────────────────────────────────────────────────────────────

AGENT_PROFILE = {
    "id": "ucp-shopping-agent-v1",
    "name": "UCP AI Shopping Agent",
    "version": "2026-04-08",
    "description": "AI agent that purchases from vending machines via UCP protocol",
    "profile_url": f"{AGENT_BASE}/profile",
    "capabilities": ["dev.ucp.shopping.cart", "dev.ucp.shopping.checkout", "dev.ucp.shopping.order"],
    "payment_support": ["dev.ucp.vending.prepaid_token"],
}

# ── Pending payment state ───────────────────────────────────────────────────────
# payment_id → {checkout_id, token, amount, currency, product_name, lane_id}
_pending: dict[str, dict] = {}

# ── UCP HTTP helpers ────────────────────────────────────────────────────────────

def _hdrs(token: str | None = None) -> dict:
    h = {
        "Content-Type":    "application/json",
        "UCP-Agent":       f'profile="{AGENT_BASE}/profile"',
        "request-id":      str(uuid.uuid4()),
        "idempotency-key": str(uuid.uuid4()),
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

async def _get(url: str, token: str | None = None) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(url, headers=_hdrs(token))
    return {"status": r.status_code, "data": r.json()}

async def _post(url: str, body: Any = None, token: str | None = None,
                form: str | None = None) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        if form:
            r = await c.post(url, content=form,
                             headers={"Content-Type": "application/x-www-form-urlencoded",
                                      "UCP-Agent": f'profile="{AGENT_BASE}/profile"'})
        elif body is not None:
            r = await c.post(url, json=body, headers=_hdrs(token))
        else:
            r = await c.post(url, headers=_hdrs(token))
    return {"status": r.status_code, "data": r.json()}

# ── Tool implementations ────────────────────────────────────────────────────────

async def tool_discover() -> dict:
    return await _get(f"{MERCHANT_URL}/.well-known/ucp")

async def tool_get_token() -> dict:
    return await _post(f"{MERCHANT_URL}/oauth/token",
                       form="grant_type=client_credentials&client_id=demo&client_secret=demo")

async def tool_browse_catalog(token: str) -> dict:
    return await _post(f"{MERCHANT_URL}/cart-sessions", token=token)

async def tool_create_checkout(token: str, lane_id: str, buyer_email: str) -> dict:
    # Discover available payment handlers from merchant profile
    profile = await _get(f"{MERCHANT_URL}/.well-known/ucp")
    handlers = profile.get("data", {}).get("payment_handlers", [])
    result = await _post(f"{MERCHANT_URL}/checkout-sessions", body={
        "line_items": [{"id": lane_id, "quantity": 1}],
        "buyer": {"email": buyer_email},
        "payment": {
            "handler_id": "alipay_aipay",  # prefer Alipay AI Pay by default
            "instrument": {},
        },
    }, token=token)
    # Attach handler list to result for agent awareness
    if result.get("data"):
        result["data"]["_available_handlers"] = handlers
    return result

async def tool_request_payment(token: str, checkout_id: str,
                                amount: int, currency: str,
                                product_name: str, lane_id: str) -> dict:
    """Store payment info and signal the UI to show a payment dialog."""
    # Fetch payment handler configs from merchant discovery
    gpay_config = None
    alipay_config = None
    try:
        profile = await _get(f"{MERCHANT_URL}/.well-known/ucp")
        handlers = profile.get("data", {}).get("payment_handlers", [])
        for h in handlers:
            if h.get("name") == "com.google.pay" or h.get("handler_id") == "google_pay":
                gpay_config = h.get("config", {})
            if h.get("name") == "com.alipay.aipay" or h.get("handler_id") == "alipay_aipay":
                alipay_config = h.get("config", {})
    except Exception:
        pass

    pid = str(uuid.uuid4())
    _pending[pid] = {
        "token":        token,
        "checkout_id":  checkout_id,
        "amount":       amount,
        "currency":     currency,
        "product_name": product_name,
        "lane_id":      lane_id,
    }
    return {
        "status": 200,
        "data": {
            "payment_id":    pid,
            "requires_user_confirmation": True,
            "amount":        amount,
            "currency":      currency,
            "product_name":  product_name,
            "google_pay":    gpay_config,
            "alipay":        alipay_config,
            "message": "Payment authorization required. Waiting for user to confirm.",
        },
    }

async def tool_complete_checkout(token: str, checkout_id: str,
                                  ap2_mandate: str | None = None) -> dict:
    """Complete checkout. If AP2 mandate is provided, include it per UCP AP2 spec."""
    body: dict = {}
    if ap2_mandate:
        # UCP AP2: checkout_mandate proves user authorized this specific checkout
        body["ap2"] = {"checkout_mandate": ap2_mandate}
    return await _post(f"{MERCHANT_URL}/checkout-sessions/{checkout_id}/complete",
                       body=body if body else None, token=token)

async def tool_track_order(token: str, order_id: str) -> dict:
    return await _get(f"{MERCHANT_URL}/orders/{order_id}", token)

# ── Tool schemas ────────────────────────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "ucp_discover",
            "description": "GET /.well-known/ucp — discover merchant capabilities and supported payment handlers.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ucp_get_token",
            "description": "POST /oauth/token — get OAuth 2.0 Bearer token via client_credentials.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ucp_browse_catalog",
            "description": "POST /v1/cart — browse available products. Returns items with lane IDs, prices, stock.",
            "parameters": {
                "type": "object",
                "properties": {"token": {"type": "string"}},
                "required": ["token"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ucp_create_checkout",
            "description": "POST /v1/checkout — create checkout session for selected item. Returns checkout ID and total price.",
            "parameters": {
                "type": "object",
                "properties": {
                    "token":       {"type": "string"},
                    "lane_id":     {"type": "string", "description": "lane_xxx from catalog"},
                    "buyer_email": {"type": "string"},
                },
                "required": ["token", "lane_id", "buyer_email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ucp_request_payment",
            "description": (
                "REQUIRED before completing checkout. Call this to request user payment authorization. "
                "Provide the checkout total from the checkout response. "
                "This pauses the flow — the user must confirm payment before checkout can complete."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "token":        {"type": "string"},
                    "checkout_id":  {"type": "string"},
                    "amount":       {"type": "integer", "description": "Total in cents, e.g. 500 for ¥5.00"},
                    "currency":     {"type": "string", "description": "e.g. CNY"},
                    "product_name": {"type": "string"},
                    "lane_id":      {"type": "string"},
                },
                "required": ["token", "checkout_id", "amount", "currency", "product_name", "lane_id"],
            },
        },
    },
]

TOOL_FNS = {
    "ucp_discover":          lambda a: tool_discover(),
    "ucp_get_token":         lambda a: tool_get_token(),
    "ucp_browse_catalog":    lambda a: tool_browse_catalog(a["token"]),
    "ucp_create_checkout":   lambda a: tool_create_checkout(a["token"], a["lane_id"], a["buyer_email"]),
    "ucp_request_payment":   lambda a: tool_request_payment(
        a["token"], a["checkout_id"], a["amount"], a["currency"], a["product_name"], a["lane_id"]
    ),
}

# ── Agent loop (Phase 1: discover → create checkout → request payment) ──────────

SYSTEM_PROMPT = f"""You are a UCP Customer Agent — an AI shopping assistant implementing UCP 2026-04-08 + AP2 Mandate protocol.
Merchant: {MERCHANT_URL}  |  Agent Profile: {AGENT_BASE}/profile

The merchant supports: Alipay AI Pay (ACT/1.0), Google Pay, Prepaid Token.
AP2 (Agent Payment Protocol) is active — all checkout responses include ap2.merchant_authorization.

When user asks to buy something, follow these steps IN ORDER:
1. ucp_discover — discover capabilities (note ap2_mandate + alipay handler)
2. ucp_get_token — OAuth 2.0 client_credentials
3. ucp_browse_catalog — POST /cart-sessions, find best match (quantity_available > 0)
4. ucp_create_checkout — POST /checkout-sessions (uses alipay_aipay handler by default)
5. ucp_request_payment — MANDATORY before completing. Provide:
   - checkout_id from the checkout response
   - amount from totals[type=total].amount (in fen/cents, e.g. 500 = ¥5.00)
   - currency: CNY
   - product_name and lane_id
   After calling this, briefly tell user the item and price. DO NOT call complete.

Key AP2 rules:
- The checkout response includes ap2.merchant_authorization (merchant signed the terms)
- The complete request will include ap2.checkout_mandate (platform proves user consent)
- This is the Alipay ACT/1.0 "意图授权凭证" flow

If out of stock or offline, explain and suggest another item.
Be concise. Always state the exact price (¥X.XX) when requesting payment.
"""

async def run_agent_phase1(user_message: str) -> AsyncGenerator[str, None]:
    client = OpenAI(api_key=GLM_API_KEY, base_url=GLM_BASE_URL)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_message},
    ]

    def sse(event: str, data: Any) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    for _ in range(8):
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.chat.completions.create(
                model=GLM_MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                stream=False,
                max_tokens=800,
            ),
        )
        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        if msg.content:
            yield sse("text", {"content": msg.content})

        if finish_reason == "stop" or not msg.tool_calls:
            break

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            yield sse("tool_call", {"id": tc.id, "name": fn_name, "args": args})

            fn = TOOL_FNS.get(fn_name)
            result = await fn(args) if fn else {"error": f"unknown tool: {fn_name}"}

            yield sse("tool_result", {
                "id": tc.id, "name": fn_name,
                "status": result.get("status"), "data": result.get("data"),
            })

            messages.append({
                "role": "tool", "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

            # If payment was requested, emit payment event and stop
            if fn_name == "ucp_request_payment":
                d = result.get("data", {})
                if d.get("requires_user_confirmation"):
                    yield sse("payment_request", {
                        "payment_id":   d["payment_id"],
                        "checkout_id":  args.get("checkout_id", ""),
                        "token":        args.get("token", ""),
                        "amount":       d["amount"],
                        "currency":     d["currency"],
                        "product_name": d["product_name"],
                        "google_pay":   d.get("google_pay"),
                        "alipay":       d.get("alipay"),
                    })
                    # Let agent add one final text response
                    continue

    yield sse("done", {})

# ── Phase 2: confirm payment → complete checkout → track order ──────────────────

async def _verify_alipay_payment(token: str, alipay_order_id: str) -> dict:
    """Server-side Alipay payment verification (安全隔离原则 — never trust frontend callback)."""
    return await _get(f"{MERCHANT_URL}/alipay/query-order/{alipay_order_id}", token)


def _build_ap2_mandate(mandate_type: str, mandate_body: dict) -> str:
    """Construct AP2 checkout_mandate (mock SD-JWT per ACT/1.0)."""
    import base64 as _b64, json as _j
    header = _b64.urlsafe_b64encode(
        b'{"alg":"ES256","typ":"ap2+sd-jwt","kid":"ucp-agent-v1"}'
    ).rstrip(b"=").decode()
    payload = _b64.urlsafe_b64encode(
        _j.dumps(mandate_body, ensure_ascii=False, sort_keys=True).encode()
    ).rstrip(b"=").decode()
    # In production: sign with agent platform private key (ES256)
    return f"{header}.{payload}.mock_platform_sig"


async def run_agent_phase2(payment_id: str, gpay_token: str | None = None,
                            alipay_order_id: str | None = None,
                            intent_credential: str | None = None) -> AsyncGenerator[str, None]:
    def sse(event: str, data: Any) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    pending = _pending.pop(payment_id, None)
    if not pending:
        yield sse("text", {"content": "⚠️ 支付信息已过期，请重新发起购买。"})
        yield sse("done", {})
        return

    token       = pending["token"]
    checkout_id = pending["checkout_id"]
    amount      = pending["amount"]
    currency    = pending["currency"]
    product     = pending["product_name"]

    # ── 安全隔离原则：服务端验证支付状态，不依赖前端反馈 ────────────────────────
    # Per Alipay AI Pay spec: "预下单+结果查询"双阶段验证机制，
    # 必须通过服务端查询确认最终交易状态，杜绝支付欺诈风险。
    if alipay_order_id:
        vid = f"alipay-verify-{uuid.uuid4()}"
        yield sse("tool_call", {"id": vid, "name": "alipay_verify_payment",
                                 "args": {"alipay_order_id": alipay_order_id,
                                          "checkout_id": checkout_id}})
        verify = await _verify_alipay_payment(token, alipay_order_id)
        yield sse("tool_result", {"id": vid, "name": "alipay_verify_payment",
                                   "status": verify["status"], "data": verify["data"]})

        vdata = verify.get("data", {})

        # Cross-check: alipay_order_id must belong to this checkout session
        if vdata.get("checkout_id") and vdata["checkout_id"] != checkout_id:
            yield sse("text", {"content": "⚠️ 支付订单与结账会话不匹配，拒绝继续。"})
            yield sse("done", {})
            return

        if verify["status"] != 200 or vdata.get("status") != "paid":
            reason = vdata.get("status", "unknown")
            yield sse("text", {"content": f"⚠️ 服务端支付验证失败（状态：{reason}），无法完成结账。"})
            yield sse("done", {})
            return

        # Use server-returned credential — override any frontend-provided value
        # (安全隔离原则：服务端凭证比前端 postMessage 更可信)
        server_credential = vdata.get("intent_credential")
        if server_credential:
            intent_credential = server_credential

    # ── AP2 mandate 构造（ACT/1.0 意图授权凭证）─────────────────────────────────
    ap2_mandate = None
    now_ts = time.time()

    if intent_credential:
        mandate_body = {
            "type":          "alipay_intent_credential",
            "act_version":   "ACT/1.0",
            "credential":    intent_credential,
            "checkout_id":   checkout_id,
            "alipay_order_id": alipay_order_id,
            "amount":        {"value": amount, "currency": currency},
            "agent_id":      AGENT_PROFILE["id"],
            "issued_by":     f"{AGENT_BASE}/profile",
            "iat":           int(now_ts),
            "exp":           int(now_ts) + 300,
            "verification":  {
                "method":    "server_query",
                "verified_at": int(now_ts),
            },
        }
        ap2_mandate = _build_ap2_mandate("alipay_intent_credential", mandate_body)
        yield sse("ap2_mandate", {
            "type":     "alipay_intent_credential",
            "act":      "ACT/1.0",
            "verified": True,
            "mandate_preview": ap2_mandate[:72] + "…",
        })

    elif gpay_token:
        mandate_body = {
            "type":       "google_pay_token",
            "token_preview": gpay_token[:20] + "…",
            "checkout_id": checkout_id,
            "agent_id":   AGENT_PROFILE["id"],
            "issued_by":  f"{AGENT_BASE}/profile",
            "iat":        int(now_ts),
        }
        ap2_mandate = _build_ap2_mandate("google_pay_token", mandate_body)

    yield sse("text", {"content": "✅ 支付已授权，正在完成结账…"})

    # Complete checkout (with AP2 mandate if present)
    cid = f"complete-{uuid.uuid4()}"
    yield sse("tool_call", {"id": cid, "name": "ucp_complete_checkout",
                             "args": {"checkout_id": checkout_id,
                                      "ap2_mandate": "included" if ap2_mandate else None}})
    result = await tool_complete_checkout(token, checkout_id, ap2_mandate=ap2_mandate)
    yield sse("tool_result", {"id": cid, "name": "ucp_complete_checkout",
                               "status": result["status"], "data": result["data"]})

    data = result.get("data", {})
    if result["status"] not in (200, 201) or data.get("status") == "error":
        msgs = data.get("messages", [{}])
        msg = msgs[0].get("content", msgs[0].get("message", "结账失败")) if msgs else "结账失败"
        yield sse("text", {"content": f"❌ 结账失败：{msg}"})
        yield sse("done", {})
        return

    order_id = data.get("order_id")
    if not order_id:
        yield sse("text", {"content": "❌ 未获取到订单ID"})
        yield sse("done", {})
        return

    # Track order
    tid = f"track-{uuid.uuid4()}"
    yield sse("tool_call", {"id": tid, "name": "ucp_track_order",
                             "args": {"order_id": order_id}})
    await asyncio.sleep(1)
    track = await tool_track_order(token, order_id)
    yield sse("tool_result", {"id": tid, "name": "ucp_track_order",
                               "status": track["status"], "data": track["data"]})

    events = track.get("data", {}).get("fulfillment", {}).get("events", [])
    event_names = [e.get("type", e.get("name", "")) for e in events]

    amount_str = f"¥{amount/100:.2f}"
    yield sse("payment_done", {
        "order_id":   order_id,
        "amount":     amount_str,
        "product":    product,
        "events":     event_names,
    })
    yield sse("text", {"content":
        f"🎉 购买成功！\n\n"
        f"商品：{product}\n"
        f"支付：{amount_str} {currency}\n"
        f"订单号：{order_id}\n"
        f"出货状态：{' → '.join(event_names) if event_names else '处理中…'}"
    })
    yield sse("done", {})

# ── FastAPI ─────────────────────────────────────────────────────────────────────

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="UCP Customer Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

@app.get("/profile")
def agent_profile():
    return AGENT_PROFILE

@app.get("/health")
def health():
    return {"ok": True, "merchant": MERCHANT_URL}

@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    msg = body.get("message", "").strip()
    if not msg:
        return {"error": "empty"}
    return StreamingResponse(run_agent_phase1(msg),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.post("/api/alipay-create-order")
async def alipay_create_order_proxy(request: Request):
    """Proxy: agent creates Alipay pre-order (alipay.trade.precreate) on behalf of user.

    Follows Alipay AI Pay MCP tool pattern:
    Input:  order_name, merchant_order_id (checkout_id), total_amount
    Output: pre-order number + cashier_url for user confirmation
    """
    body = await request.json()
    # Use checkout_id as merchant_order_id for cross-reference verification
    checkout_id = body.get("checkout_id", "")
    token = body.get("token", "")
    result = await _post(f"{MERCHANT_URL}/alipay/create-order", body={
        "checkout_id":       checkout_id,
        "merchant_order_id": checkout_id,   # Alipay field: merchant自定义订单号
        "amount":            body.get("amount"),
        "currency":          body.get("currency", "CNY"),
        "product_name":      body.get("product_name", ""),
        "agent_pay_info": {                  # Alipay AI Pay required field
            "agent_type": "AI_AGENT",
            "agent_id":   AGENT_PROFILE["id"],
            "agent_name": AGENT_PROFILE["name"],
        },
    }, token=token)
    return result

@app.post("/api/confirm-payment")
async def confirm_payment(request: Request):
    body = await request.json()
    pid                = body.get("payment_id", "")
    gpay_token         = body.get("gpay_token")
    alipay_order_id    = body.get("alipay_order_id")
    intent_credential  = body.get("intent_credential")
    return StreamingResponse(
        run_agent_phase2(pid, gpay_token=gpay_token,
                         alipay_order_id=alipay_order_id,
                         intent_credential=intent_credential),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.get("/", response_class=FileResponse)
def index():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


if __name__ == "__main__":
    print(f"\n  UCP Customer Agent")
    print(f"  → http://localhost:{PORT}")
    print(f"  → Merchant: {MERCHANT_URL}")
    print(f"  → Profile:  {AGENT_BASE}/profile\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
