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
from tools.supply_chain import (
    get_inventory, check_low_stock,
    create_purchase_order, get_purchase_order_status,
    preview_daily_order, trigger_daily_order, list_supply_chain_events,
)
from tools.slack_notify import notify_ops, notify_restock, notify_goods_arrived, notify_payment_failed
from tools.welfare import get_welfare_balance, deduct_welfare, set_welfare_quota

SYSTEM_PROMPT = """你是 UCP Vending Agent，AI 驱动的自动贩卖机智能助手。

═══ 标准购买流程（严格按序执行）═══
1. ucp_discover        — 发现商户能力和支付处理器
2. ucp_get_token       — 获取 OAuth 2.0 Bearer Token
3. get_welfare_balance — 查询用户企业福利余额（user_id 来自上下文）
4. ucp_browse_catalog  — 浏览商品目录，理解用户模糊需求后选最佳匹配
5. ucp_create_checkout — 为选定货道创建结账会话
6. ucp_request_payment — 发起支付请求（含福利余额提示）
   ⚠️  此工具调用后流程自动暂停，等待用户完成支付，不要继续调用其他工具。

═══ 支付确认后（系统自动恢复）═══
7. ucp_complete_checkout — 服务端验证支付 + AP2 mandate（含生物识别）+ 完成结账 → 获取取货码
   ⚠️  将 ucp_request_payment 返回的 biometric 字段传入此工具
8. deduct_welfare         — 若有福利抵扣，完成扣款（商品出货后调用）
9. notify_ops             — 通知 Slack 出货成功

═══ 缺货处理 ═══
• 货柜无此商品 → 先调 get_inventory 确认库存状态
• 再调 query_supplier_sku 查询用友宝供应商 SKU 目录
• 向用户展示：价格、品牌、预计到货天数
• 用户确认预订 → create_preorder 记录
• notify_restock 发送 Slack 补货通知

═══ 供应链管理（运营模式）═══
• check_low_stock       — 查看当前低库存 / 缺货货道
• preview_daily_order   — 预览今日补货计划
• trigger_daily_order   — 执行每日自动补货（汇总 PO 提交给供应商）
• create_purchase_order — 向指定供应商手动创建采购单
• get_purchase_order_status — 查看采购单进度

═══ 企业福利规则 ═══
• 余额 > 0：在支付卡片中注明"可抵扣企业福利 ¥X.XX"
• 出货成功后调用 deduct_welfare（先出货再扣款，赊账模式）
• 若余额不足：部分抵扣 + 个人支付差额

═══ 生物识别（Kiosk 模式）═══
• user_id 可能来自人脸识别，置信度已包含在会话上下文中
• AP2 checkout_mandate 包含生物验证记录，代表用户的生物授权
• 无需再次索取用户身份确认

═══ 输出规范 ═══
• 价格用 ¥X.XX 格式，对话简洁友好
• 不重复叙述工具调用细节，直接告知结果
• 取货码用代码格式展示：`XXXXXXXX`"""

ALL_TOOLS = [
    # UCP 购物流程
    ucp_discover, ucp_get_token, ucp_browse_catalog,
    ucp_create_checkout, ucp_request_payment, ucp_complete_checkout,
    # 供应链（用友宝）
    query_supplier_sku, create_preorder, list_preorders,
    # 供应链管理
    get_inventory, check_low_stock, create_purchase_order,
    get_purchase_order_status, preview_daily_order,
    trigger_daily_order, list_supply_chain_events,
    # 通知
    notify_ops, notify_restock, notify_goods_arrived, notify_payment_failed,
    # 企业福利
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
        if not msgs or not isinstance(msgs[0], SystemMessage):
            user_id = state.get("user_id", "guest")
            bio = state.get("biometric") or {}
            bio_note = ""
            if bio.get("confidence"):
                bio_note = (
                    f"\n生物识别：方式={bio.get('method','unknown')} "
                    f"置信度={bio.get('confidence', 0):.0%}"
                    + (" 声纹验证✓" if bio.get("voice_verified") else "")
                )
            sys = SystemMessage(content=SYSTEM_PROMPT + f"\n\n当前用户 ID：{user_id}{bio_note}")
            msgs = [sys] + msgs
        return {"messages": [llm_with_tools.invoke(msgs)]}

    def should_continue(state: VendingState) -> str:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return END

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
