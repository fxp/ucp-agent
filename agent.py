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
from fastapi.responses import HTMLResponse, StreamingResponse
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

    # AP2: construct checkout_mandate (simulated SD-JWT)
    # In production: platform signs checkout response + user authorization
    import base64 as _b64m, json as _jm
    ap2_mandate = None
    if intent_credential:
        # Alipay path: use intent_credential as the AP2 payment mandate
        mandate_body = {
            "type": "alipay_intent_credential",
            "credential": intent_credential,
            "checkout_id": checkout_id,
            "issued_by": f"{AGENT_BASE}/profile",
            "act_version": "ACT/1.0",
        }
        ap2_mandate = "ap2_sd_jwt." + _b64m.urlsafe_b64encode(
            _jm.dumps(mandate_body, ensure_ascii=False).encode()
        ).rstrip(b"=").decode() + ".mock_platform_sig"
        yield sse("ap2_mandate", {"type": "alipay_intent_credential",
                                   "mandate_preview": ap2_mandate[:60] + "…"})
    elif gpay_token:
        mandate_body = {"type": "google_pay_token", "token_preview": gpay_token[:20],
                        "checkout_id": checkout_id}
        ap2_mandate = "ap2_sd_jwt." + _b64m.urlsafe_b64encode(
            _jm.dumps(mandate_body, ensure_ascii=False).encode()
        ).rstrip(b"=").decode() + ".mock_platform_sig"

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

app = FastAPI(title="UCP Customer Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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
    """Proxy: agent creates Alipay pre-order on behalf of user."""
    body = await request.json()
    token = body.get("token", "")
    result = await _post(f"{MERCHANT_URL}/alipay/create-order", body={
        "checkout_id":  body.get("checkout_id"),
        "amount":       body.get("amount"),
        "currency":     body.get("currency", "CNY"),
        "product_name": body.get("product_name", ""),
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

@app.get("/", response_class=HTMLResponse)
def playground():
    return _HTML

# ── Playground HTML ─────────────────────────────────────────────────────────────

_HTML = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>UCP Customer Agent Playground</title>
<script async src="https://pay.google.com/gp/p/js/pay.js" onload="_initGPay()"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0d1117;color:#e6edf3;height:100vh;display:flex;flex-direction:column}}
header{{background:#161b22;border-bottom:1px solid #30363d;padding:12px 24px;display:flex;align-items:center;gap:10px;flex-shrink:0}}
header h1{{font-size:15px;font-weight:700;color:#58a6ff}}
.badge{{font-size:10px;padding:2px 8px;border-radius:10px;font-weight:700}}
.bblue{{background:#1a3a5c;color:#58a6ff}}.bgreen{{background:#1a3a1a;color:#3fb950}}.byellow{{background:#3b2f00;color:#e3b341}}
.profile-url{{font-size:11px;color:#6e7681;margin-left:auto;font-family:monospace}}

.main{{display:grid;grid-template-columns:1fr 400px;flex:1;overflow:hidden}}

/* Chat */
.chat{{display:flex;flex-direction:column;border-right:1px solid #21262d}}
.msgs{{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:14px}}
.msg{{display:flex;gap:10px;animation:fi .2s ease}}
@keyframes fi{{from{{opacity:0;transform:translateY(4px)}}to{{opacity:1}}}}
.av{{width:28px;height:28px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0;margin-top:2px}}
.msg.user .av{{background:#1a3a5c}}.msg.agent .av{{background:#1a3a1a}}
.mb{{flex:1}}.role{{font-size:11px;font-weight:700;letter-spacing:.04em;color:#6e7681;margin-bottom:4px;text-transform:uppercase}}
.txt{{font-size:13px;line-height:1.65;white-space:pre-wrap;word-break:break-word}}
.msg.user .txt{{color:#cae8ff}}

/* Payment card */
.pay-card{{background:#1a2535;border:1px solid #1f6feb;border-radius:10px;padding:16px;margin-top:4px;max-width:360px}}
.pay-card h3{{font-size:13px;font-weight:700;color:#58a6ff;margin-bottom:10px;display:flex;align-items:center;gap:6px}}
.pay-item{{display:flex;justify-content:space-between;font-size:12px;color:#8b949e;margin-bottom:4px}}
.pay-total{{display:flex;justify-content:space-between;font-size:16px;font-weight:800;color:#e6edf3;border-top:1px solid #30363d;padding-top:10px;margin-top:6px}}
.pay-currency{{font-size:11px;color:#6e7681;margin-left:4px}}
.pay-handler{{font-size:11px;color:#3fb950;margin-top:8px;display:flex;align-items:center;gap:4px}}
.pay-btns{{display:flex;gap:8px;margin-top:14px}}
.btn-pay{{flex:1;padding:10px;background:#1f6feb;color:white;border:none;border-radius:7px;font-size:13px;font-weight:700;cursor:pointer;transition:.15s}}
.btn-pay:hover{{background:#388bfd}}
.btn-cancel{{padding:10px 16px;background:#21262d;color:#8b949e;border:1px solid #30363d;border-radius:7px;font-size:13px;cursor:pointer}}
.btn-cancel:hover{{background:#30363d}}
.btn-gpay{{width:100%;padding:10px;background:#000;color:#fff;border:none;border-radius:7px;font-size:13px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;margin-bottom:6px}}
.btn-gpay:hover{{background:#1a1a1a}}
.btn-alipay{{width:100%;padding:11px;background:linear-gradient(135deg,#1677ff,#0958d9);color:#fff;border:none;border-radius:7px;font-size:13px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;margin-bottom:6px}}
.btn-alipay:hover{{opacity:.9}}
.alipay-cashier{{width:100%;border-radius:10px;overflow:hidden;border:1px solid #1f6feb;margin-top:10px;display:none}}
.alipay-cashier.show{{display:block}}
.alipay-cashier iframe{{width:100%;height:380px;border:none;display:block}}
.ap2-badge{{display:inline-flex;align-items:center;gap:4px;font-size:10px;padding:2px 7px;border-radius:10px;background:#1a2535;color:#58a6ff;font-weight:600;border:1px solid #1f6feb;margin-top:6px}}

/* Order done card */
.order-card{{background:#1a3a1a;border:1px solid #238636;border-radius:10px;padding:14px;margin-top:4px;max-width:360px}}
.order-card h3{{font-size:13px;font-weight:700;color:#3fb950;margin-bottom:8px}}
.order-id{{font-family:monospace;font-size:11px;color:#8b949e;word-break:break-all;margin-bottom:8px}}
.events{{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}}
.ev-chip{{font-size:10px;padding:2px 8px;border-radius:10px;background:#1a4731;color:#3fb950;font-weight:600}}

.suggestions{{padding:0 20px 14px;display:flex;gap:8px;flex-wrap:wrap}}
.sug{{background:#161b22;border:1px solid #30363d;border-radius:20px;padding:5px 14px;font-size:12px;color:#8b949e;cursor:pointer;transition:.15s}}
.sug:hover{{border-color:#58a6ff;color:#58a6ff}}

.input-bar{{border-top:1px solid #21262d;padding:14px;display:flex;gap:10px;background:#161b22;flex-shrink:0}}
.input-bar textarea{{flex:1;background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:9px 12px;color:#e6edf3;font-size:13px;resize:none;font-family:inherit;min-height:56px}}
.input-bar textarea:focus{{outline:none;border-color:#58a6ff}}
.input-bar button{{padding:0 18px;background:#1f6feb;color:white;border:none;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer;align-self:flex-end;height:36px}}
.input-bar button:disabled{{opacity:.4;cursor:not-allowed}}

/* Log panel */
.log-panel{{display:flex;flex-direction:column;background:#0d1117}}
.log-hdr{{padding:11px 14px;border-bottom:1px solid #21262d;font-size:11px;font-weight:700;letter-spacing:.08em;color:#6e7681;text-transform:uppercase;display:flex;align-items:center;gap:6px;flex-shrink:0}}
.log-entries{{flex:1;overflow-y:auto;padding:10px}}
.log-entry{{margin-bottom:7px;border:1px solid #21262d;border-radius:6px;overflow:hidden;animation:fi .2s ease}}
.le-hdr{{padding:6px 10px;display:flex;align-items:center;gap:7px;cursor:pointer}}
.le-hdr:hover{{background:#161b22}}
.meth{{font-family:monospace;font-size:10px;font-weight:700;padding:1px 5px;border-radius:3px}}
.mGET{{background:#1a3a5c;color:#58a6ff}}.mPOST{{background:#1a3a1a;color:#3fb950}}
.lpath{{font-family:monospace;font-size:11px;color:#8b949e;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.lstat{{font-size:10px;font-weight:700;margin-left:auto}}.s2{{color:#3fb950}}.s4{{color:#f85149}}
.larrow{{color:#6e7681;font-size:10px;transition:transform .15s}}
.log-entry.open .larrow{{transform:rotate(90deg)}}
.le-body{{display:none;padding:8px 10px;border-top:1px solid #21262d;background:#0a0e14}}
.log-entry.open .le-body{{display:block}}
.le-body pre{{font-family:monospace;font-size:10px;color:#8b949e;white-space:pre-wrap;word-break:break-all;max-height:180px;overflow-y:auto}}
.calling{{background:#3b2f00;color:#e3b341;font-size:9px;padding:1px 6px;border-radius:3px;font-weight:700;font-family:monospace}}

.status-bar{{padding:7px 12px;background:#161b22;border-top:1px solid #21262d;font-size:11px;color:#6e7681;display:flex;align-items:center;gap:5px;flex-shrink:0}}
.dot{{width:6px;height:6px;border-radius:50%}}.idle{{background:#6e7681}}.thinking{{background:#e3b341;animation:p .8s infinite}}.done{{background:#3fb950}}
@keyframes p{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
::-webkit-scrollbar{{width:4px}}::-webkit-scrollbar-thumb{{background:#30363d;border-radius:2px}}
</style>
</head>
<body>
<header>
  <span style="font-size:18px">🤖</span>
  <h1>UCP Customer Agent</h1>
  <span class="badge bblue">GLM-4</span>
  <span class="badge bgreen">UCP 2026-04-08</span>
  <span class="badge byellow">Vending Machine</span>
  <span class="profile-url">profile: {AGENT_BASE}/profile</span>
</header>
<div class="main">
  <div class="chat">
    <div class="msgs" id="msgs">
      <div class="msg agent">
        <div class="av">🤖</div>
        <div class="mb">
          <div class="role">Agent</div>
          <div class="txt">你好！我是 UCP 购物 Agent，由 GLM-4 驱动。

我会通过 UCP 协议完整走完支付链路：
🔍 发现商家 → 🔑 OAuth 认证 → 🛒 浏览商品 → 📋 创建结账 → 💳 支付确认 → 🤖 触发出货 → 📦 追踪履约

告诉我你想要什么！</div>
        </div>
      </div>
    </div>
    <div class="suggestions" id="sugs">
      <div class="sug" onclick="suggest(this)">帮我买一瓶可乐</div>
      <div class="sug" onclick="suggest(this)">买矿泉水</div>
      <div class="sug" onclick="suggest(this)">有什么饮料？</div>
      <div class="sug" onclick="suggest(this)">帮我买绿茶</div>
    </div>
    <div class="input-bar">
      <textarea id="inp" placeholder="告诉 Agent 你想买什么…" rows="2"
        onkeydown="if(event.key==='Enter'&&!event.shiftKey){{event.preventDefault();send()}}"></textarea>
      <button id="sbtn" onclick="send()">发送</button>
    </div>
  </div>

  <div class="log-panel">
    <div class="log-hdr">
      <span>📡</span> Protocol Flow Log
      <button onclick="clearLog()" style="margin-left:auto;background:none;border:1px solid #30363d;color:#6e7681;padding:2px 8px;border-radius:4px;cursor:pointer;font-size:10px">Clear</button>
    </div>
    <div class="log-entries" id="log"><div style="color:#6e7681;font-size:12px;text-align:center;padding:40px 20px" id="lempty">Protocol calls will appear here</div></div>
    <div class="status-bar" id="sbar">
      <div class="dot idle" id="sdot"></div>
      <span id="stxt">Idle</span>
    </div>
  </div>
</div>

<script>
let busy = false;
const MNAMES = {{
  ucp_discover:'GET /.well-known/ucp', ucp_get_token:'POST /oauth/token',
  ucp_browse_catalog:'POST /cart-sessions', ucp_create_checkout:'POST /checkout-sessions',
  ucp_request_payment:'💳 Payment Request · AP2',
  ucp_complete_checkout:'POST /checkout-sessions/{{id}}/complete',
  ucp_track_order:'GET /orders/{{id}}',
  ap2_mandate:'🔐 AP2 Mandate · ACT/1.0',
}};
const MTYPES = {{
  ucp_discover:'GET', ucp_get_token:'POST', ucp_browse_catalog:'POST',
  ucp_create_checkout:'POST', ucp_request_payment:'POST',
  ucp_complete_checkout:'POST', ucp_track_order:'GET',
  ap2_mandate:'AP2',
}};

function setStatus(s,t){{
  document.getElementById('sdot').className='dot '+s;
  document.getElementById('stxt').textContent=t;
}}

function addMsg(role, content=''){{
  const el=document.getElementById('msgs');
  const d=document.createElement('div');
  d.className='msg '+role;
  const av=role==='user'?'👤':'🤖';
  const lbl=role==='user'?'You':'Agent';
  d.innerHTML=`<div class="av">${{av}}</div><div class="mb"><div class="role">${{lbl}}</div><div class="txt" id="t-${{Date.now()}}">${{esc(content)}}</div></div>`;
  el.appendChild(d);
  el.scrollTop=99999;
  return d.querySelector('.txt');
}}

function esc(s){{return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}

function addPayCard(pid, amount, currency, product, gpayConfig, alipayConfig){{
  const el=document.getElementById('msgs');
  const d=document.createElement('div');
  d.className='msg agent';
  const sym=currency==='CNY'?'¥':'$';
  const amtStr=`${{sym}}${{(amount/100).toFixed(2)}}`;
  const gpayBtn = gpayConfig ? `
    <button class="btn-gpay" id="gpay-${{pid}}" onclick="gpayPay('${{pid}}', ${{amount}}, '${{currency}}', ${{JSON.stringify(gpayConfig).replace(/'/g,"\\'")}})">
      <img src="https://pay.google.com/about/static/images/logos/googlepay_button_content.svg" alt="Google Pay" style="height:20px;vertical-align:middle;margin-right:6px">Pay
    </button>` : '';
  const alipayBtn = alipayConfig ? `
    <button class="btn-alipay" onclick="alipayPay('${{pid}}', ${{amount}}, '${{currency}}', '${{esc(product)}}', ${{JSON.stringify(alipayConfig).replace(/'/g,"\\'")}})">
      <span style="font-size:16px">支</span> 支付宝 AI付 · ACT/1.0
    </button>` : '';
  const hasAlt = !!(gpayConfig || alipayConfig);
  d.innerHTML=`<div class="av">💳</div><div class="mb"><div class="role">Payment Request</div>
    <div class="pay-card" id="paycard-${{pid}}">
      <h3>💳 支付确认</h3>
      <div class="pay-item"><span>商品</span><span>${{esc(product)}}</span></div>
      <div class="pay-item"><span>商家</span><span>WM800 Vending Machine</span></div>
      <div class="pay-item"><span>协议</span><span>UCP AP2 · 意图授权凭证</span></div>
      <div class="pay-total"><span>合计</span><span>${{amtStr}}<span class="pay-currency">${{currency}}</span></span></div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">
        ${{alipayConfig ? '<span class="ap2-badge">🔐 ACT/1.0</span>' : ''}}
        <span class="ap2-badge">🛡️ AP2 Mandate</span>
        ${{gpayConfig ? '<span class="ap2-badge" style="color:#4285f4;border-color:#4285f4">G Pay</span>' : ''}}
      </div>
      <div class="pay-btns" id="pbtns-${{pid}}" style="margin-top:14px">
        ${{alipayBtn}}
        ${{gpayBtn}}
        <button class="btn-pay" onclick="confirmPay('${{pid}}', this)">${{hasAlt ? '💸 Prepaid Token' : `确认支付 ${{amtStr}}`}}</button>
        <button class="btn-cancel" onclick="cancelPay('${{pid}}', this)">取消</button>
      </div>
      <div class="alipay-cashier" id="alipay-cashier-${{pid}}">
        <iframe id="alipay-frame-${{pid}}" src="about:blank"></iframe>
      </div>
    </div></div>`;
  el.appendChild(d);
  el.scrollTop=99999;
  // Render official Google Pay button if available
  if(gpayConfig && window._gpayClient) {{
    try {{
      const btn = window._gpayClient.createButton({{
        onClick: () => gpayPay(pid, amount, currency, gpayConfig),
        buttonType: 'pay',
        buttonColor: 'black',
      }});
      btn.style.cssText='width:100%;border-radius:7px;overflow:hidden;margin-bottom:6px';
      const btns = document.getElementById('pbtns-'+pid);
      if(btns) btns.insertBefore(btn, btns.firstChild);
      document.getElementById('gpay-'+pid)?.remove();
    }} catch(e) {{ console.warn('gpay button render failed', e); }}
  }}
}}

// Google Pay integration
let _gpayReady = false;
function _initGPay() {{
  if(_gpayReady || !window.google?.payments?.api?.PaymentsClient) return;
  window._gpayClient = new google.payments.api.PaymentsClient({{environment: 'TEST'}});
  _gpayReady = true;
}}

async function gpayPay(pid, amount, currency, cfg) {{
  _initGPay();
  if(!window._gpayClient) {{
    alert('Google Pay SDK not loaded. Check network connection.');
    return;
  }}
  const totalPrice = (amount/100).toFixed(2);
  const paymentDataRequest = {{
    apiVersion: cfg.api_version || 2,
    apiVersionMinor: cfg.api_version_minor || 0,
    merchantInfo: {{
      merchantId: cfg.merchant_info?.merchant_id || 'TEST',
      merchantName: cfg.merchant_info?.merchant_name || 'WM800 Vending',
    }},
    allowedPaymentMethods: cfg.allowed_payment_methods || [{{
      type: 'CARD',
      parameters: {{allowedAuthMethods:['PAN_ONLY','CRYPTOGRAM_3DS'], allowedCardNetworks:['VISA','MASTERCARD']}},
      tokenizationSpecification: {{type:'PAYMENT_GATEWAY', parameters:{{gateway:'example', gatewayMerchantId:'exampleGatewayMerchantId'}}}},
    }}],
    transactionInfo: {{
      totalPriceStatus: 'FINAL',
      totalPrice: totalPrice,
      currencyCode: currency || 'CNY',
      countryCode: 'CN',
    }},
  }};

  const card = document.getElementById('paycard-'+pid);
  try {{
    const paymentData = await window._gpayClient.loadPaymentData(paymentDataRequest);
    const gpayToken = paymentData?.paymentMethodData?.tokenizationData?.token || 'gpay-mock-token';
    // Disable buttons
    if(card) {{ card.style.opacity='.5'; card.style.pointerEvents='none'; }}
    setStatus('thinking','Google Pay authorised, completing…');
    // Phase 2 with gpay token
    await _doConfirmPayment(pid, gpayToken);
  }} catch(err) {{
    if(err.statusCode === 'CANCELED') return;
    console.error('Google Pay error', err);
    addMsg('agent', `Google Pay 失败：${{err.message || err.statusCode}}`);
  }}
}}

async function _doConfirmPayment(pid, gpayToken, alipayOrderId, intentCredential) {{
  const agEl = addMsg('agent','');
  let agTxt = '';
  await streamSSE(
    fetch('/api/confirm-payment',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{
        payment_id: pid,
        gpay_token: gpayToken || null,
        alipay_order_id: alipayOrderId || null,
        intent_credential: intentCredential || null,
      }})}}),
    {{
      text: p=>{{ agTxt+=p.content; agEl.textContent=agTxt; document.getElementById('msgs').scrollTop=99999; }},
      tool_call: p=>{{ setStatus('thinking',`${{p.name}}…`); addLog(p.id,p.name,p.args); }},
      tool_result: p=>{{ setStatus('thinking',`${{p.name}} → ${{p.status}}`); updLog(p.id,p.status,p.data); }},
      payment_done: p=>{{ addOrderCard(p.order_id, p.amount, p.product, p.events); }},
      ap2_mandate: p=>{{ addLog('ap2-'+Date.now(), 'ap2_mandate', p); updLog('ap2-'+Date.now(), 200, p); }},
      done: _=>{{ setStatus('done','Done ✓'); setTimeout(()=>setStatus('idle','Idle'),3000); }},
    }}
  );
}}

// Alipay AI Pay integration
let _pendingAlipayPid = null;
async function alipayPay(pid, amount, currency, product, cfg) {{
  _pendingAlipayPid = pid;
  const card = document.getElementById('paycard-'+pid);
  const cashierDiv = document.getElementById('alipay-cashier-'+pid);
  const frame = document.getElementById('alipay-frame-'+pid);
  // Disable other buttons
  const btns = document.querySelectorAll('#pbtns-'+pid+' button');
  btns.forEach(b => b.disabled = true);
  setStatus('thinking', '创建支付宝预下单…');

  // Create pre-order via agent API
  const pending = Object.values(window._pendingPayments||{{}}).find(p=>p.pid===pid) || {{}};
  const r = await fetch('/api/alipay-create-order', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{
      checkout_id: pending.checkout_id || '',
      amount, currency, product_name: product,
    }})
  }});
  const order = await r.json();
  if(!order.data?.cashier_url && !order.cashier_url) {{
    addMsg('agent', '支付宝预下单失败，请重试');
    btns.forEach(b=>b.disabled=false); return;
  }}
  const cashierUrl = order.data?.cashier_url || order.cashier_url;
  const alipayOrderId = order.data?.alipay_order_id || order.alipay_order_id;

  // Show cashier iframe
  cashierDiv.classList.add('show');
  frame.src = cashierUrl;
  setStatus('thinking', '等待用户支付确认…');

  // Listen for payment confirmation from iframe
  window.addEventListener('message', async function handler(e) {{
    if(e.data?.type !== 'alipay_paid' || e.data?.alipay_order_id !== alipayOrderId) return;
    window.removeEventListener('message', handler);
    cashierDiv.classList.remove('show');
    if(card) {{ card.style.opacity='.5'; card.style.pointerEvents='none'; }}
    setStatus('thinking', '支付宝已确认，正在完成结账…');
    await _doConfirmPayment(pid, null, alipayOrderId, e.data.intent_credential);
  }});
}}

function addOrderCard(order_id, amount, product, events){{
  const el=document.getElementById('msgs');
  const d=document.createElement('div');
  d.className='msg agent';
  const chips=events.map(e=>`<span class="ev-chip">${{esc(e)}}</span>`).join('');
  d.innerHTML=`<div class="av">📦</div><div class="mb"><div class="role">Fulfillment</div>
    <div class="order-card">
      <h3>✅ 出货完成</h3>
      <div class="pay-item"><span>商品</span><span>${{esc(product)}}</span></div>
      <div class="pay-item"><span>支付金额</span><span>${{amount}}</span></div>
      <div class="order-id">${{esc(order_id)}}</div>
      <div class="events">${{chips}}</div>
    </div></div>`;
  el.appendChild(d);
  el.scrollTop=99999;
}}

// Log
function addLog(id, name, args){{
  document.getElementById('lempty')?.remove();
  const el=document.getElementById('log');
  const meth=MTYPES[name]||'POST';
  const path=MNAMES[name]||name;
  const d=document.createElement('div');
  d.className='log-entry'; d.id='le-'+id;
  d.innerHTML=`<div class="le-hdr" onclick="this.parentElement.classList.toggle('open')">
    <span class="meth m${{meth}}">${{meth}}</span>
    <span class="lpath">${{esc(path)}}</span>
    <span class="calling" id="lb-${{id}}">calling…</span>
    <span class="larrow">▶</span>
  </div>
  <div class="le-body"><pre id="lbody-${{id}}">${{args&&Object.keys(args).length?pjson(args):'(no body)'}}</pre></div>`;
  el.appendChild(d);
  el.scrollTop=99999;
}}
function updLog(id, status, data){{
  const b=document.getElementById('lb-'+id);
  const p=document.getElementById('lbody-'+id);
  if(b){{const ok=status>=200&&status<300; b.className='lstat '+(ok?'s2':'s4'); b.textContent=status;}}
  if(p&&data) p.innerHTML=pjson(data);
}}
function pjson(o){{
  try{{return JSON.stringify(o,null,2)
    .replace(/"([^"]+)":/g,'<span style="color:#79c0ff">"$1"</span>:')
    .replace(/: "([^"]*)"/g,': <span style="color:#a5d6ff">"$1"</span>')
    .replace(/: (\\d+\\.?\\d*)/g,': <span style="color:#f2cc60">$1</span>');}}catch{{return String(o);}}
}}
function clearLog(){{document.getElementById('log').innerHTML='<div style="color:#6e7681;font-size:12px;text-align:center;padding:40px 20px" id="lempty">Protocol calls will appear here</div>';}}

// Streaming helper
async function streamSSE(fetchPromise, handlers){{
  const resp = await fetchPromise;
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  while(true){{
    const {{done, value}} = await reader.read();
    if(done) break;
    buf += dec.decode(value, {{stream:true}});
    const parts = buf.split('\\n\\n');
    buf = parts.pop() || '';
    for(const part of parts){{
      if(!part.trim()) continue;
      let ev='message', data='';
      for(const line of part.split('\\n')){{
        if(line.startsWith('event: ')) ev=line.slice(7);
        if(line.startsWith('data: ')) data=line.slice(6);
      }}
      if(!data) continue;
      try{{
        const p=JSON.parse(data);
        handlers[ev]?.(p);
      }}catch{{}}
    }}
  }}
}}

// Phase 1: chat
async function send(){{
  const inp=document.getElementById('inp');
  const msg=inp.value.trim();
  if(!msg||busy) return;
  busy=true; inp.value='';
  document.getElementById('sbtn').disabled=true;
  document.getElementById('sugs').style.display='none';
  addMsg('user', msg);
  const agEl=addMsg('agent','');
  let agTxt='';
  setStatus('thinking','Agent is thinking…');

  await streamSSE(
    fetch('/api/chat',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{message:msg}})}}),
    {{
      text: p=>{{ agTxt+=p.content; agEl.textContent=agTxt; document.getElementById('msgs').scrollTop=99999; }},
      tool_call: p=>{{ setStatus('thinking',`Calling ${{p.name}}…`); addLog(p.id,p.name,p.args); }},
      tool_result: p=>{{ setStatus('thinking',`${{p.name}} → ${{p.status}}`); updLog(p.id,p.status,p.data); }},
      payment_request: p=>{{
        setStatus('thinking','Awaiting payment confirmation…');
        // Store checkout context for alipay pre-order
        window._pendingPayments = window._pendingPayments || {{}};
        window._pendingPayments[p.payment_id] = {{
          pid: p.payment_id, checkout_id: p.checkout_id || '',
          token: p.token || '', amount: p.amount, currency: p.currency,
        }};
        addPayCard(p.payment_id, p.amount, p.currency, p.product_name, p.google_pay, p.alipay);
      }},
      done: _=>{{ setStatus('done','Done ✓'); setTimeout(()=>setStatus('idle','Idle'),3000); }},
    }}
  );
  busy=false;
  document.getElementById('sbtn').disabled=false;
}}

// Phase 2: confirm payment (Prepaid path)
async function confirmPay(pid, btn){{
  btn.disabled=true; btn.textContent='处理中…';
  const card=document.getElementById('paycard-'+pid);
  if(card){{ card.style.opacity='.5'; card.style.pointerEvents='none'; }}
  setStatus('thinking','Processing payment…');
  await _doConfirmPayment(pid, null);
}}

function cancelPay(pid, btn){{
  const card=document.getElementById('paycard-'+pid);
  if(card) card.parentElement.parentElement.remove();
  addMsg('agent','已取消支付。如需重新购买，请再次告诉我。');
  setStatus('idle','Idle');
}}

function suggest(el){{ document.getElementById('inp').value=el.textContent; send(); }}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print(f"\n  UCP Customer Agent")
    print(f"  → http://localhost:{PORT}")
    print(f"  → Merchant: {MERCHANT_URL}")
    print(f"  → Profile:  {AGENT_BASE}/profile\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
