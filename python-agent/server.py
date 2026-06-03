"""FastAPI server — UCP Vending Agent (LangGraph + LangSmith)."""
from __future__ import annotations
import json, logging, os, uuid
from typing import AsyncIterator

# ── LangSmith tracing ─────────────────────────────────────────────────────────
os.environ.setdefault("LANGCHAIN_TRACING_V2", os.getenv("LANGSMITH_ENABLED", "true"))
os.environ.setdefault("LANGCHAIN_PROJECT",    "ucp-vending-agent")
# LANGCHAIN_API_KEY must be set in .env / environment

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel

from agent.graph import get_graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("server")

app = FastAPI(title="UCP Vending Agent", version="2.0")
app.add_middleware(CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

UCP_MOCK_URL = os.getenv("UCP_MOCK_URL", "https://ucp-mock.fxp007.workers.dev")


# ── SSE helpers ───────────────────────────────────────────────────────────────
def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _parse_tool_output(raw: object) -> object:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {"raw": raw}
    return raw


# ── Request models ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    user_id: str = "guest"
    thread_id: str | None = None


class ConfirmPaymentRequest(BaseModel):
    thread_id: str
    payment_id: str = ""
    gpay_token: str | None = None
    alipay_order_id: str | None = None   # receipt_id (intent credential JWT)
    intent_credential: str | None = None


class AlipayCreateOrderRequest(BaseModel):
    checkout_id: str
    token: str
    amount: int
    currency: str = "CNY"
    product_name: str = ""


# ── Event streaming ───────────────────────────────────────────────────────────
async def _stream_graph(
    input_data: dict | Command,
    config: dict,
    thread_id: str,
) -> AsyncIterator[str]:
    """Stream LangGraph events as SSE, detect payment interrupts."""
    graph = get_graph()
    interrupt_data: dict | None = None

    try:
        async for event in graph.astream_events(input_data, config, version="v2"):
            ev   = event.get("event", "")
            name = event.get("name", "")
            data = event.get("data", {})

            # LLM streaming text
            if ev == "on_chat_model_stream":
                chunk = data.get("chunk")
                content = (getattr(chunk, "content", None) or
                           (chunk.get("content") if isinstance(chunk, dict) else None) or "")
                if content:
                    yield sse("text", {"content": content})

            # Tool invocations
            elif ev == "on_tool_start":
                yield sse("tool_call", {"name": name, "args": data.get("input", {})})

            # Tool results
            elif ev == "on_tool_end":
                output = _parse_tool_output(data.get("output", ""))
                yield sse("tool_result", {"name": name, "data": output})

                # Detect pickup_code from ucp_complete_checkout
                if name == "ucp_complete_checkout" and isinstance(output, dict):
                    if output.get("pickup_code"):
                        yield sse("payment_done", {
                            "order_id":    output.get("order_id", ""),
                            "pickup_code": output.get("pickup_code"),
                            "pickup_url":  output.get("pickup_url"),
                            "amount":      "",
                            "product":     "",
                            "events":      [],
                        })

            # Interrupt detection via chain stream
            elif ev == "on_chain_stream" and name == "LangGraph":
                chunk = data.get("chunk", {})
                if isinstance(chunk, dict) and "__interrupt__" in chunk:
                    for intr in (chunk["__interrupt__"] or []):
                        val = getattr(intr, "value", None) if hasattr(intr, "value") else intr
                        if isinstance(val, dict) and val.get("requires_user_confirmation"):
                            interrupt_data = val
                            break

    except Exception as e:
        log.exception("graph stream error")
        yield sse("text", {"content": f"❌ 错误：{e}"})

    # Fallback: check state for interrupt if not caught in stream
    if interrupt_data is None:
        try:
            state = await graph.aget_state(config)
            for task in (state.tasks or []):
                for intr in (getattr(task, "interrupts", None) or []):
                    val = getattr(intr, "value", None)
                    if isinstance(val, dict) and val.get("requires_user_confirmation"):
                        interrupt_data = val
                        break
        except Exception:
            pass

    if interrupt_data:
        yield sse("payment_request", {"thread_id": thread_id, **interrupt_data})

    yield sse("done", {"thread_id": thread_id})


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.post("/api/chat")
async def chat(req: ChatRequest):
    thread_id = req.thread_id or str(uuid.uuid4())
    config    = {"configurable": {"thread_id": thread_id}}
    input_data = {
        "messages": [HumanMessage(content=req.message)],
        "user_id":  req.user_id,
    }

    async def gen():
        async for chunk in _stream_graph(input_data, config, thread_id):
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/confirm-payment")
async def confirm_payment(req: ConfirmPaymentRequest):
    config    = {"configurable": {"thread_id": req.thread_id}}
    # Resume LangGraph from interrupt with payment result
    resume_val = {
        "payment_method":    "alipay" if req.alipay_order_id else "google_pay",
        "alipay_receipt_id": req.alipay_order_id    or "",
        "intent_credential": req.intent_credential  or "",
        "gpay_token":        req.gpay_token         or "",
    }

    async def gen():
        async for chunk in _stream_graph(Command(resume=resume_val), config, req.thread_id):
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/alipay-create-order")
async def alipay_create_order(req: AlipayCreateOrderRequest):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{UCP_MOCK_URL}/alipay/create-order",
            headers={"Authorization": f"Bearer {req.token}",
                     "Content-Type": "application/json"},
            json={
                "checkout_id":     req.checkout_id,
                "merchant_order_id": req.checkout_id,
                "amount":          req.amount,
                "currency":        req.currency,
                "product_name":    req.product_name,
                "agent_pay_info":  {
                    "agent_type": "AI_AGENT",
                    "agent_id":   "ucp-vending-agent-v1",
                    "agent_name": "UCP Vending Agent",
                },
            })
        return r.json()


@app.get("/health")
async def health():
    return {"ok": True, "tracing": os.getenv("LANGCHAIN_TRACING_V2", "false")}


# ── Static frontend ────────────────────────────────────────────────────────────
_static = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8090, reload=True)
