---
name: supply-chain
description: >
  操作 UCP 贩卖机供应链系统（supply-chain-mock）。
  管理供应商、SKU、贩卖机、货道、库存、采购单、预订单。
  TRIGGER: 供应商 / 补货 / 采购 / 库存 / 贩卖机配置 / 预订单 / SKU 管理。
triggers:
  - 供应商
  - 补货
  - 采购单
  - 库存
  - 贩卖机配置
  - 货道
  - 预订单
  - SKU
  - 进货
  - supply-chain
  - supply chain
---

# Supply Chain Skill

**Base URL**: `https://supply-chain-mock.fxp007.workers.dev`  
**Auth**: 无（内网操作，不对外暴露）  
**Content-Type**: `application/json`

## 数据模型

```
supplier:{id}              供应商
sku:{id}                   商品 SKU
machine:{id}               贩卖机
lane:{machineId}:{laneId}  货道配置（SKU + 价格）
inv:{machineId}:{laneId}   库存数量
```

## 供应商 / Suppliers

| 操作 | 方法 | 路径 |
|------|------|------|
| 列表 | GET  | `/suppliers` |
| 详情 | GET  | `/suppliers/{id}` |
| 创建 | POST | `/suppliers` |
| 更新 | PUT  | `/suppliers/{id}` |
| 删除 | DELETE | `/suppliers/{id}` |

```jsonc
// POST /suppliers
{ "id": "sup-001", "name": "娃哈哈", "contact": "sales@wahaha.com", "lead_days": 3 }
```

## SKU 管理

| 操作 | 方法 | 路径 |
|------|------|------|
| 列表 | GET  | `/skus` |
| 详情 | GET  | `/skus/{id}` |
| 创建 | POST | `/skus` |
| 更新 | PUT  | `/skus/{id}` |
| 删除 | DELETE | `/skus/{id}` |

```jsonc
// POST /skus
{
  "sku_id": "NF-001", "name": "矿泉水 550ml",
  "supplier_id": "sup-001",
  "cost_fen": 80,      // 进价（分）
  "retail_fen": 200,   // 零售价（分）
  "moq": 24            // 最小订货量
}
```

## 贩卖机 / Machines

| 操作 | 方法 | 路径 |
|------|------|------|
| 列表 | GET  | `/machines` |
| 详情+库存概况 | GET | `/machines/{id}` |
| 创建 | POST | `/machines` |
| 库存明细 | GET | `/machines/{id}/inventory` |

```jsonc
// POST /machines
{ "id": "vm-001", "name": "1楼大厅", "location": "1F Main Hall" }
```

## 货道 / Lanes

| 操作 | 方法 | 路径 |
|------|------|------|
| 列表 | GET  | `/lanes?machine_id={id}` |
| 详情 | GET  | `/lanes/{machineId}/{laneId}` |
| 创建 | POST | `/lanes` |
| 更新 | PUT  | `/lanes/{machineId}/{laneId}` |
| 删除 | DELETE | `/lanes/{machineId}/{laneId}` |

```jsonc
// POST /lanes
{
  "machine_id": "vm-001", "lane_id": "A1",
  "sku_id": "NF-001", "price_fen": 200, "currency": "CNY",
  "min_qty": 5, "capacity": 20
}
```

## 库存 / Inventory

| 操作 | 方法 | 路径 |
|------|------|------|
| 按机器查询 | GET | `/inventory?machine_id={id}[&low_stock_only=true]` |
| 跨机器搜索 | GET | `/inventory/search?name={keyword}` |
| 设置库存 | PUT | `/inventory/{machineId}/{laneId}` |

```jsonc
// PUT /inventory/vm-001/A1
{ "qty": 20 }
```

## 采购单 / Purchase Orders

| 操作 | 方法 | 路径 |
|------|------|------|
| 列表 | GET  | `/purchase-orders` |
| 详情 | GET  | `/purchase-orders/{id}` |
| 创建 | POST | `/purchase-orders` |
| 更新状态 | PUT | `/purchase-orders/{id}` |

```jsonc
// POST /purchase-orders
{
  "supplier_id": "sup-001",
  "items": [{ "sku_id": "NF-001", "qty": 48, "unit_cost_fen": 80 }]
}
// PUT /purchase-orders/{id} — 收货
{ "status": "received" }  // → 自动补充对应货道库存
```

## 预订单 / Preorders

| 操作 | 方法 | 路径 |
|------|------|------|
| 列表 | GET  | `/preorders` |
| 创建 | POST | `/preorders` |

```jsonc
// POST /preorders
{
  "sku_id": "NF-001", "sku_name": "矿泉水 550ml",
  "qty": 1, "user_id": "xiaofei-001",
  "notify_channel": "slack"
}
```

## 每日补货 / Daily Order

| 操作 | 方法 | 路径 |
|------|------|------|
| 预览 | GET  | `/internal/daily-order/preview` |
| 执行 | POST | `/internal/daily-order` |

```jsonc
// POST /internal/daily-order
{ "dry_run": false }  // 汇总低库存 + 预订单 → 提交采购单
```

## 典型流程

### 新机器上线
```bash
# 1. 创建贩卖机
POST /machines  {"id":"vm-004","name":"5楼咖啡区","location":"5F Coffee Corner"}

# 2. 添加货道（每条货道对应一个 SKU）
POST /lanes  {"machine_id":"vm-004","lane_id":"A1","sku_id":"CC-001","price_fen":350,...}

# 3. 设置初始库存
PUT /inventory/vm-004/A1  {"qty":15}
```

### 补货流程
```bash
# 1. 查看低库存
GET /inventory?machine_id=vm-001&low_stock_only=true

# 2. 创建采购单
POST /purchase-orders  {"supplier_id":"sup-001","items":[...]}

# 3. 收货（自动补充库存）
PUT /purchase-orders/{id}  {"status":"received"}
```

### 缺货处理（用户下单触发）
```bash
# 1. 查供应商有没有货
GET /skus?keyword=矿泉水

# 2. 创建预订单
POST /preorders  {"sku_id":"NF-001","user_id":"xxx","qty":1}

# 3. 采购到货后 PUT purchase-order status=received → 自动补库存 + 通知用户
```

## 参考
- 种子脚本: `scripts/seed.sh`
- 端到端测试: `scripts/test-e2e.sh`
- 消费者模拟: `scripts/sim-xiaofei.py`
