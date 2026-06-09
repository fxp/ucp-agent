"""员工企业福利余额管理。
生产环境替换为 KV / 数据库即可；接口保持不变。
"""
from __future__ import annotations
import json, os
from langchain_core.tools import tool

# ¥50.00 default per employee per month
DEFAULT_QUOTA_YUAN = float(os.getenv("WELFARE_DEFAULT_QUOTA_YUAN", "50.0"))

# In-memory store: user_id → {quota, used, remaining}
_store: dict[str, dict[str, float]] = {}


def _account(user_id: str) -> dict[str, float]:
    if user_id not in _store:
        _store[user_id] = {
            "quota": DEFAULT_QUOTA_YUAN,
            "used": 0.0,
            "remaining": DEFAULT_QUOTA_YUAN,
        }
    return _store[user_id]


@tool
def get_welfare_balance(user_id: str) -> str:
    """查询员工企业福利余额（¥）。购物前调用，余额 > 0 时提示用户可抵扣。"""
    w = _account(user_id)
    return json.dumps({
        "user_id": user_id,
        "quota_yuan": w["quota"],
        "used_yuan": round(w["used"], 2),
        "remaining_yuan": round(w["remaining"], 2),
        "currency": "CNY",
        "hint": f"企业福利余额 ¥{w['remaining']:.2f}，可抵扣消费" if w["remaining"] > 0 else "福利余额已用完",
    })


@tool
def deduct_welfare(user_id: str, amount_cents: int) -> str:
    """从员工企业福利余额扣除金额（单位：分）。在商品出货后调用。"""
    w = _account(user_id)
    amount = amount_cents / 100
    if amount <= 0:
        return json.dumps({"ok": False, "error": "金额必须大于 0"})
    deduct = min(amount, w["remaining"])  # 只扣可用余额部分
    if deduct <= 0:
        return json.dumps({"ok": False, "error": "福利余额不足，请使用其他支付方式"})
    w["used"] += deduct
    w["remaining"] -= deduct
    remainder = round(amount - deduct, 2)
    return json.dumps({
        "ok": True,
        "deducted_yuan": round(deduct, 2),
        "remaining_yuan": round(w["remaining"], 2),
        "personal_payment_yuan": remainder,
        "message": (
            f"企业福利已扣 ¥{deduct:.2f}"
            + (f"，个人支付 ¥{remainder:.2f}" if remainder > 0 else "，全额福利抵扣")
        ),
    })


@tool
def set_welfare_quota(user_id: str, quota_yuan: float) -> str:
    """设置员工月度福利额度（管理员专用）。"""
    w = _account(user_id)
    w["quota"] = quota_yuan
    w["remaining"] = max(0.0, quota_yuan - w["used"])
    return json.dumps({"ok": True, "user_id": user_id, "new_quota_yuan": quota_yuan})
