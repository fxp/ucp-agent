# ucp-agent

GLM-4 驱动的 UCP 买方 Agent。实现完整的 Cart → Checkout → Payment → Fulfillment 链路，内置支付确认 UI。

```bash
pip install fastapi uvicorn openai httpx
python agent.py          # → http://localhost:8090
```

---

## 架构概览

```
用户浏览器
    │  SSE 流 (text / tool_call / payment_request / payment_done)
    ▼
agent.py  (FastAPI + GLM-4 tool-use)
    │
    ├── UCP Merchant ──► fxp/vending-protocol  (WM800 适配器 / Mock)
    │       Cart → Checkout → Complete → Order
    │
    ├── Alipay AI Pay ──► 支付宝 ACT/1.0 意图授权凭证
    │       预下单 → 收银台 → intent_credential → AP2 mandate
    │
    ├── Google Pay ──► Google Pay Web API (TEST 环境)
    │       loadPaymentData → gpay_token → AP2 mandate
    │
    ├── 友宝 Vending API ──► 友宝-智谱接口  [⚠️ 待接入]
    │       下单出货，替代 WM800 直连串口
    │
    └── CRM  [⚠️ 未实现]
            订单同步 / 会员积分 / 售后
```

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AGENT_PORT` | `8090` | Agent 监听端口 |
| `MERCHANT_URL` | `http://localhost:8080` | UCP Merchant 地址（见下） |
| `GLM_API_KEY` | *(内置测试 key)* | 智谱 BigModel API Key |

---

## 外部服务

### 1. UCP Merchant — `fxp/vending-protocol`

**状态：已接入**

Agent 通过 UCP 协议与 Merchant 通信，不直接控制硬件。

| 端点 | 说明 |
|------|------|
| `GET /.well-known/ucp` | 发现商家能力 + payment handlers |
| `POST /oauth/token` | OAuth 2.0 client_credentials |
| `POST /cart-sessions` | 浏览商品目录 |
| `POST /checkout-sessions` | 创建结账会话 |
| `POST /checkout-sessions/{id}/complete` | 完成结账（含 AP2 mandate） |
| `GET /orders/{id}` | 追踪订单履约状态 |

**本地启动 Mock（无需硬件）：**

```bash
git clone https://github.com/fxp/vending-protocol
cd vending-protocol/adapters/ucp/mock
pip install -r ../requirements.txt
python server.py          # → http://localhost:8080
```

**生产适配器（需 WM800 串口）：**

```bash
cd vending-protocol/adapters/ucp/server
python app.py
```

参考：[fxp/vending-protocol](https://github.com/fxp/vending-protocol) · `adapters/ucp/references/mapping.md`（UCP ↔ WM800 字段映射）

---

### 2. 支付宝 AI Pay — ACT/1.0 意图授权凭证

**状态：已接入（AP2 mandate 流程完整，收银台 iframe 待真实对接）**

支付宝 AI 付款协议，允许 Agent 代用户完成支付授权，核心是「意图授权凭证（intent_credential）」。

**流程：**

```
Agent 调用 ucp_create_checkout（handler: alipay_aipay）
    → Merchant 返回 alipay.cashier_url
    → 前端打开收银台 iframe
    → 用户确认 → iframe postMessage({type:'alipay_paid', intent_credential})
    → Agent Phase 2：用 intent_credential 构造 AP2 checkout_mandate
    → POST /checkout-sessions/{id}/complete  {ap2: {checkout_mandate}}
```

**AP2 mandate 结构（当前为 mock SD-JWT）：**

```json
{
  "type": "alipay_intent_credential",
  "credential": "<intent_credential from Alipay>",
  "checkout_id": "...",
  "issued_by": "http://localhost:8090/profile",
  "act_version": "ACT/1.0"
}
```

**文档参考：**
- [支付宝开放平台 — AI 代付](https://opendocs.alipay.com/open/ai-pay)（ACT/1.0 意图授权凭证接入指南）
- [UCP AP2 规范](https://ucp.dev/specs/ap2)（checkout_mandate SD-JWT 签名格式）

**Merchant 侧需配置（`catalog.example.json`）：**

```json
{
  "handler_id": "alipay_aipay",
  "name": "com.alipay.aipay",
  "config": {
    "cashier_url_template": "https://mcashier.alipay.com/...",
    "app_id": "<your_alipay_app_id>"
  }
}
```

---

### 3. Google Pay

**状态：已接入（TEST 环境）**

通过 Google Pay Web API 完成支付，返回 `paymentData.paymentMethodData.tokenizationData.token`，同样包装为 AP2 mandate 传给 Merchant。

```javascript
// 前端调用
const paymentData = await _gpayClient.loadPaymentData(paymentDataRequest);
const gpayToken = paymentData.paymentMethodData.tokenizationData.token;
// → /api/confirm-payment {gpay_token}
```

**文档：** [Google Pay Web developer guide](https://developers.google.com/pay/api/web/guides/tutorial)

---

### 4. 友宝 Vending API

**状态：⚠️ 待接入**

友宝智谱联名接口，用于通过云端 API 直接下单出货，不经过本地 WM800 串口。接入后可替代 `fxp/vending-protocol` 的生产适配器，实现云端直控。

**接口文档：** [友宝-智谱接口文档](https://www.yuque.com/wuxinyu-ulbxn/ra9g1x)（语雀，需申请权限）

**测试环境：**
- 测试客编：`NOT74038`
- 可用此客编调用所有接口进行联调

**预期接入方式：**

```python
# 计划新增 tool：youbao_create_order
# Agent 在 complete_checkout 成功后调用，触发友宝实际出货
async def tool_youbao_create_order(
    client_id: str,          # NOT74038（测试）
    machine_id: str,         # 设备编号
    lane_id: str,            # 货道号
    order_id: str,           # UCP order_id，用于对账
) -> dict: ...
```

**接入优先级：** 高（当前 Mock 无法驱动真实设备，友宝 API 是生产路径）

---

### 5. CRM

**状态：⚠️ 未实现**

计划在订单完成后同步至 CRM，实现：
- 会员积分累计
- 消费记录追踪
- 售后工单创建

**待确定：** CRM 系统选型（自研 / 第三方）、接入时机（Phase 2 `payment_done` 后异步写入）

---

## 支付流程总览

```
Phase 1（Agent 主动）
─────────────────────────────────────────────────────
ucp_discover → ucp_get_token → ucp_browse_catalog
→ ucp_create_checkout → ucp_request_payment
                              │
                              └── SSE: payment_request → 前端显示支付卡片
                                        │
                              ┌─────────┴──────────┐
                        支付宝 AI Pay          Google Pay
                     收银台 iframe         loadPaymentData
                     intent_credential      gpay_token
                              └─────────┬──────────┘
                                        ▼
Phase 2（用户确认后）
─────────────────────────────────────────────────────
构造 AP2 checkout_mandate（mock SD-JWT）
→ ucp_complete_checkout {ap2: {checkout_mandate}}
→ ucp_track_order
→ [友宝 API 出货]  ← 待接入
→ [CRM 写入]       ← 未实现
→ SSE: payment_done
```

---

## 本地完整联调

```bash
# Terminal 1 — UCP Mock Merchant（无需硬件）
cd vending-protocol/adapters/ucp/mock && python server.py

# Terminal 2 — Agent
cd ucp-agent
GLM_API_KEY=your_key MERCHANT_URL=http://localhost:8080 python agent.py

# 打开 http://localhost:8090，输入"帮我买一瓶可乐"
```
