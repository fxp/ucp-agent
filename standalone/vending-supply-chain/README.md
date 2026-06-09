# vending-supply-chain

Standalone supply chain management API for vending machine networks.  
Built as a single Cloudflare Worker with KV for persistence. No database, no server.

## API overview

| Endpoint | Description |
|----------|-------------|
| `GET  /health` | Health check |
| `GET/POST /suppliers` | Supplier CRUD |
| `GET/POST /suppliers/{id}/skus` | SKUs for a supplier |
| `GET/POST /skus`, `/skus/{id}` | SKU CRUD |
| `GET/POST /machines`, `/machines/{id}` | Machine CRUD |
| `GET/POST /machines/{id}/lanes` | Lane config |
| `GET /machines/{id}/inventory` | Inventory per machine |
| `GET /inventory?machine_id=` | Inventory (flat) |
| `GET /inventory/search?name=` | Cross-machine item search |
| `POST /inventory/restock` | Manual restock |
| `GET/POST /purchase-orders` | PO CRUD |
| `POST /purchase-orders/{id}/advance` | Advance PO status |
| `GET/POST /preorders` | Preorder (out-of-stock demand) |
| `POST /preorders/{id}/notify` | Mark preorder notified |
| `GET /internal/daily-order/preview` | Preview what would be ordered |
| `POST /internal/daily-order` | Trigger order (dry_run supported) |

## PO status flow

```
draft → submitted → acknowledged → shipped → received → stocked
```

Advancing to `stocked` automatically updates inventory quantities and marks matching preorders as `stock_arrived`.

## Quick start (local dev)

```bash
npm install
npx wrangler dev          # → http://localhost:8787
./seed.sh                  # seed suppliers, SKUs, machines, lanes
```

## Deploy to Cloudflare Workers

```bash
# 1. Create KV namespace
npx wrangler kv namespace create SC_KV

# 2. Copy and fill wrangler config
cp wrangler.example.jsonc wrangler.jsonc
# Edit: set account_id and kv_namespaces[0].id

# 3. Deploy
npm run deploy

# 4. Seed data
BASE_URL=https://vending-supply-chain.<your-subdomain>.workers.dev ./seed.sh
```

## Cron auto-order

The Worker includes a cron trigger (`0 2 * * *` — daily at 02:00 UTC) that automatically creates purchase orders for lanes below `min_qty`. Orders below the supplier's `min_order_yuan` are skipped.

Preview before it runs:

```bash
curl http://localhost:8787/internal/daily-order/preview?machine_id=vm-001
```

## KV schema

| Key pattern | Value type | Description |
|-------------|------------|-------------|
| `supplier:{id}` | Supplier | Supplier record |
| `machine:{id}` | Machine | Machine record |
| `sku:{id}` | Sku | SKU record |
| `lane:{machineId}:{laneId}` | LaneConfig | Lane config |
| `inv:{machineId}:{laneId}` | `{qty, last_restocked_at}` | Live inventory |
| `po:{id}` | PurchaseOrder | PO record |
| `pre:{id}` | Preorder | Preorder record |
