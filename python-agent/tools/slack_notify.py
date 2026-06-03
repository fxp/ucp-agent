"""Slack 通知工具。
未设置 SLACK_BOT_TOKEN 时降级到本地日志，方便本地开发。
扩展：添加新渠道只需实现 NotificationBackend 协议并注册到 BACKENDS。
"""
from __future__ import annotations
import json, logging, os
from langchain_core.tools import tool
import httpx

logger = logging.getLogger("slack_notify")

SLACK_TOKEN   = os.getenv("SLACK_BOT_TOKEN", "")
DEFAULT_CHAN  = os.getenv("SLACK_OPS_CHANNEL", "#vending-ops")
RESTOCK_CHAN  = os.getenv("SLACK_RESTOCK_CHANNEL", "#vending-restock")
PAYMENT_CHAN  = os.getenv("SLACK_PAYMENT_CHANNEL", "#vending-payments")

_USE_MOCK = not SLACK_TOKEN


async def _post(channel: str, text: str, blocks: list | None = None) -> dict:
    if _USE_MOCK:
        logger.info("[Slack mock] %s → %s", channel, text)
        return {"ok": True, "mock": True, "channel": channel}
    async with httpx.AsyncClient(timeout=10) as c:
        payload: dict = {"channel": channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        r = await c.post("https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
            json=payload)
        return r.json()


@tool
async def notify_ops(message: str, channel: str = "") -> str:
    """发送运营通知到 Slack #vending-ops（出货成功、设备异常等）。"""
    result = await _post(channel or DEFAULT_CHAN, message)
    return json.dumps(result)


@tool
async def notify_restock(
    sku_name: str,
    preorder_id: str,
    quantity: int,
    eta_days: int,
    user_id: str = "",
) -> str:
    """
    发送补货通知到 Slack #vending-restock（有用户下预订单时触发）。
    包含 SKU 信息、数量和预计到货时间。
    """
    text = (
        f"🔄 *补货请求*\n"
        f"商品：{sku_name}\n"
        f"数量：×{quantity}\n"
        f"预订单：`{preorder_id}`\n"
        f"预计到货：{eta_days} 天\n"
        f"用户：{user_id or '匿名'}"
    )
    result = await _post(RESTOCK_CHAN, text)
    return json.dumps(result)


@tool
async def notify_payment_failed(user_id: str, order_id: str, amount_cents: int) -> str:
    """发送扣款失败通知到 Slack #vending-payments，供运营跟进。"""
    text = (
        f"⚠️ *扣款失败*\n"
        f"用户：{user_id}\n"
        f"订单：`{order_id}`\n"
        f"金额：¥{amount_cents/100:.2f}"
    )
    result = await _post(PAYMENT_CHAN, text)
    return json.dumps(result)


@tool
async def notify_goods_arrived(
    sku_name: str,
    machine_location: str,
    user_ids: list[str] | None = None,
) -> str:
    """商品到货上架后，通知相关预订用户（Slack）。也可扩展到企业微信/飞书。"""
    users_str = "、".join(user_ids or []) or "所有预订用户"
    text = (
        f"📦 *到货通知*\n"
        f"商品：{sku_name}\n"
        f"已上架：{machine_location}\n"
        f"通知用户：{users_str}"
    )
    result = await _post(DEFAULT_CHAN, text)
    return json.dumps(result)
