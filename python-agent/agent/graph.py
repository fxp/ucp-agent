"""LangGraph agent — UCP Vending Machine AI 购物助手。"""
from __future__ import annotations
import os
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode

from .state import VendingState
from tools.ucp import (
    ucp_discover, ucp_get_token, ucp_browse_catalog,
    ucp_create_checkout, ucp_request_payment, ucp_complete_checkout,
)
from tools.yonyou import query_supplier_sku, create_preorder, list_preorders
from tools.slack_notify import notify_ops, notify_restock, notify_goods_arrived, notify_payment_failed
from tools.welfare import get_welfare_balance, deduct_welfare, set_welfare_quota

SYSTEM_PROMPT = """你是 UCP Vending Agent，AI 驱动的自动贩卖机智能采购助手。

═══ 标准购买流程（严格按序执行）═══
1. ucp_discover        — 发现商户能力和支付处理器
2. ucp_get_token       — 获取 OAuth 2.0 Bearer Token
3. get_welfare_balance — 查询用户企业福利余额（user_id 来自上下文）
4. ucp_browse_catalog  — 浏览商品目录，理解用户模糊需求后选最佳匹配
5. ucp_create_checkout — 为选定货道创建结账会话
6. ucp_request_payment — 发起支付请求（含福利余额提示）
   ⚠️  此工具调用后流程自动暂停，等待用户完成支付，不要继续调用其他工具。

═══ 支付确认后（系统自动恢复）═══
7. ucp_complete_checkout — 服务端验证支付 + AP2 mandate + 完成结账 → 获取取货码
8. deduct_welfare         — 若有福利抵扣，完成扣款（商品出货后调用）
9. notify_ops             — 通知 Slack 出货成功

═══ 缺货处理 ═══
• 货柜无此商品 → 调用 query_supplier_sku 查询用友宝供应商
• 向用户展示：价格、品牌、预计到货天数
• 用户确认预订 → create_preorder 记录
• notify_restock 发送 Slack 补货通知

═══ 企业福利规则 ═══
• 余额 > 0：在支付卡片中注明"可抵扣企业福利 ¥X.XX"
• 出货成功后调用 deduct_welfare（先出货再扣款，赊账模式）
• 若余额不足：部分抵扣 + 个人支付差额

═══ 输出规范 ═══
• 价格用 ¥X.XX 格式，对话简洁友好
• 不重复叙述工具调用细节，直接告知结果
• 取货码用代码格式展示：`XXXXXXXX`"""

ALL_TOOLS = [
    ucp_discover, ucp_get_token, ucp_browse_catalog,
    ucp_create_checkout, ucp_request_payment, ucp_complete_checkout,
    query_supplier_sku, create_preorder, list_preorders,
    notify_ops, notify_restock, notify_goods_arrived, notify_payment_failed,
    get_welfare_balance, deduct_welfare, set_welfare_quota,
]

_checkpointer = MemorySaver()
_graph = None


def _build() -> object:
    llm = ChatOpenAI(
        base_url=os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
        model=os.getenv("LLM_MODEL", "glm-4-flash"),
        api_key=os.getenv("GLM_API_KEY", ""),
        temperature=0.1,
        max_tokens=1200,
    )
    llm_with_tools = llm.bind_tools(ALL_TOOLS)

    def agent_node(state: VendingState) -> dict:
        msgs = list(state["messages"])
        # Prepend system prompt if not present
        if not msgs or not isinstance(msgs[0], SystemMessage):
            user_id = state.get("user_id", "guest")
            sys = SystemMessage(content=SYSTEM_PROMPT + f"\n\n当前用户 ID：{user_id}")
            msgs = [sys] + msgs
        return {"messages": [llm_with_tools.invoke(msgs)]}

    def should_continue(state: VendingState) -> str:
        last = state["messages"][-1]
        calls = getattr(last, "tool_calls", None)
        if not calls:
            return END
        return "tools"

    g = StateGraph(VendingState)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(ALL_TOOLS))
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile(checkpointer=_checkpointer)


def get_graph():
    global _graph
    if _graph is None:
        _graph = _build()
    return _graph
