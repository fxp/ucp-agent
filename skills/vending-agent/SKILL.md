---
name: vending-agent
description: >
  与已部署的 UCP 贩卖机 AI Agent 交互 —— 消费者购买流程 + 供应链管理。
  覆盖 Kiosk（贩卖机旁）和 Online（在线购物）两种模式。
  TRIGGER: 贩卖机购物 / UCP agent / 测试购买流程 / 模拟用户 / kiosk。
triggers:
  - 贩卖机购物
  - UCP agent
  - 购买流程
  - kiosk
  - 模拟用户
  - vending agent
  - ucp-agent
---

# Vending Agent Skill

**Agent URL**: `https://ucp-agent.fxp007.workers.dev`  
**Mock UCP**: `https://ucp-mock.fxp007.workers.dev`  
**Supply Chain**: `https://supply-chain-mock.fxp007.workers.dev`  
**UI**:
- `/` — 在线购物助手（Online）
- `/kiosk` — 贩卖机模式（Kiosk）
- `/vending` — 运营监控面板

## 两种模式

### Kiosk 模式（用户在贩卖机旁）
- URL 传入 `machine_id`，Agent 知道具体机器，直接浏览商品
- 系统提示：「机器已确定，无需询问，直接进入商品浏览」
- 购买成功后 15 秒自动重置对话

### Online 模式（用户不在贩卖机旁）
- 用户提到具体商品 → `find_item` 跨机器搜索 → 告知哪台有货
- 用户未提商品 → `list_machines` 展示列表
- 不得直接 `list_machines` 再让用户猜哪台有货

## 消费者购买流程

遵循 [ucp-checkout-session](../../../agentic-commerce-skills/skills/03-checkout/ucp-checkout-session/SKILL.md) 规范：

```
find_item / list_machines              ← ucp-product-discovery (vending adaptation)
  ↓
ucp_discover                           ← GET /.well-known/ucp
  ↓
ucp_get_token                          ← ucp-identity-linking (guest OAuth)
  ↓
get_welfare_balance                    ← 企业福利余额查询
  ↓
ucp_browse_catalog(machine_id)         ← 浏览机器商品目录（vending 自定义端点）
  ↓
ucp_create_checkout(lane_id)           ← POST /checkout-sessions
  ↓
[可选] ucp_update_checkout(discount_code)  ← PUT /checkout-sessions/{id}  (ucp-discount)
  ↓
ucp_request_payment                    ← ap2-payment-mandate + 支付暂停
  ↓
[用户确认支付]
  ↓
POST /api/confirm-payment              ← phase2: AP2 mandate + complete
  ↓
payment_done event                     ← ucp-order-management (取货码)
```

## API 端点

### Chat（SSE 流式）
```bash
POST /api/chat
Content-Type: application/json

{
  "message":    "我想买矿泉水",
  "user_id":    "xiaofei-001",
  "thread_id":  "uuid",          # 对话历史（KV，TTL 1h）
  "machine_id": "vm-001"         # Kiosk 模式传入；Online 留空
}
```

**SSE 事件类型：**
| event | payload | 说明 |
|-------|---------|------|
| `text` | `{content: "..."}` | Agent 文字回复（增量） |
| `tool_call` | `{id, name, args}` | 工具调用（调试用） |
| `tool_result` | `{id, name, status, data}` | 工具结果 |
| `payment_request` | `{payment_id, amount, currency, product_name, ...}` | 暂停等待支付 |
| `payment_done` | `{order_id, amount, product, pickup_code, pickup_url}` | 出货成功 |
| `done` | `{}` | 流结束 |

### 支付确认
```bash
POST /api/confirm-payment
{ "payment_id": "...", "user_id": "xxx",
  "gpay_token": null, "alipay_order_id": null, "intent_credential": null }
```

### 其他
```bash
GET  /health              # 服务状态
GET  /profile             # UCP Agent Profile
GET  /api/faces           # 人脸录入列表（demo）
POST /api/faces/{user_id} # 录入人脸特征
```

## 工具清单

| 工具 | Skill | 端点 |
|------|-------|------|
| `find_item` | vending adaptation | `GET /inventory/search` |
| `list_machines` | ucp-product-discovery | `GET /machines` |
| `ucp_discover` | ucp-product-discovery | `GET /.well-known/ucp` |
| `ucp_get_token` | ucp-identity-linking | `POST /oauth/token` |
| `ucp_browse_catalog` | vending adaptation | `POST /cart-sessions` |
| `ucp_create_checkout` | ucp-checkout-session | `POST /checkout-sessions` |
| `ucp_get_checkout` | ucp-checkout-session | `GET /checkout-sessions/{id}` |
| `ucp_update_checkout` | ucp-checkout-session + ucp-discount | `PUT /checkout-sessions/{id}` |
| `ucp_cancel_checkout` | ucp-checkout-session | `POST /checkout-sessions/{id}/cancel` |
| `ucp_request_payment` | ap2-payment-mandate | 内部，签名并暂停 |
| `get_welfare_balance` | supply-chain | KV 福利余额 |
| `query_supplier_sku` | supply-chain | `GET /skus` |
| `create_preorder` | supply-chain | `POST /preorders` |
| `get_inventory` | supply-chain | `GET /inventory` |
| `preview_daily_order` | supply-chain | `GET /internal/daily-order/preview` |
| `trigger_daily_order` | supply-chain | `POST /internal/daily-order` |
| `notify_ops` | supply-chain | Slack |

## 模拟消费者测试

```bash
python3 scripts/sim-xiaofei.py
# 5 步测试：缺货告知 → 购买可乐 → 支付确认 → 库存扣减 → 历史召回
```

## 折扣码（Demo）

| 码 | 折扣 |
|----|------|
| `SAVE10` | 九折 |
| `VEND20` | 八折 |

用法：「我有优惠码 SAVE10」→ Agent 调 `ucp_update_checkout(discount_code="SAVE10")`

## 参考
- 供应链操作：[supply-chain skill](../supply-chain/SKILL.md)
- UCP checkout：[ucp-checkout-session](../../agentic-commerce-skills/skills/03-checkout/ucp-checkout-session/SKILL.md)
- AP2 支付：[ap2-payment-mandate](../../agentic-commerce-skills/skills/03-checkout/ap2-payment-mandate/SKILL.md)
- 折扣：[ucp-discount](../../agentic-commerce-skills/skills/03-checkout/ucp-discount/SKILL.md)
