#!/usr/bin/env bash
# Seed initial suppliers, SKUs, machine and lanes.
# Usage:
#   ./seed.sh                                # localhost:8787
#   BASE_URL=https://sc.example.workers.dev ./seed.sh

set -euo pipefail
BASE_URL=${BASE_URL:-http://localhost:8787}

post() {
  local path=$1; shift
  curl -sf -X POST "$BASE_URL$path" \
    -H "Content-Type: application/json" \
    -d "$1"
}

echo "=== Suppliers ==="
post /suppliers '{"id":"NFSQ",  "name":"农夫山泉",   "lead_time_days":2,"min_order_yuan":300}'
post /suppliers '{"id":"DFSY",  "name":"东方树叶",   "lead_time_days":3,"min_order_yuan":500}'
post /suppliers '{"id":"COCA",  "name":"可口可乐",   "lead_time_days":3,"min_order_yuan":400}'
post /suppliers '{"id":"YOUBAO","name":"友宝通用补货","lead_time_days":1,"min_order_yuan":0}'

echo ""
echo "=== SKUs ==="
post /skus '{"sku_id":"water-500",    "name":"农夫山泉 500ml",    "cost_fen":150,"retail_fen":200,"moq":48,"supplier_id":"NFSQ"}'
post /skus '{"sku_id":"green-tea-500","name":"东方树叶绿茶 500ml","cost_fen":350,"retail_fen":500,"moq":24,"supplier_id":"DFSY"}'
post /skus '{"sku_id":"cola-330",     "name":"可口可乐 330ml",    "cost_fen":250,"retail_fen":350,"moq":24,"supplier_id":"COCA"}'
post /skus '{"sku_id":"coffee-250",   "name":"雀巢咖啡 250ml",    "cost_fen":400,"retail_fen":600,"moq":12,"supplier_id":"YOUBAO"}'
post /skus '{"sku_id":"energy-330",   "name":"红牛 330ml",        "cost_fen":450,"retail_fen":700,"moq":12,"supplier_id":"YOUBAO"}'

echo ""
echo "=== Machine ==="
post /machines '{"id":"vm-001","name":"1F 茶水间","location":"1楼"}'
post /machines '{"id":"vm-002","name":"2F 走廊",  "location":"2楼"}'
post /machines '{"id":"vm-003","name":"3F 休息区","location":"3楼"}'

echo ""
echo "=== Lanes (vm-001) ==="
post /machines/vm-001/lanes '{"lane_id":"101","sku_id":"water-500",    "name":"农夫山泉 500ml",    "price_fen":200,"min_qty":5,"capacity":20}'
post /machines/vm-001/lanes '{"lane_id":"102","sku_id":"green-tea-500","name":"东方树叶绿茶 500ml","price_fen":500,"min_qty":3,"capacity":15}'
post /machines/vm-001/lanes '{"lane_id":"103","sku_id":"cola-330",     "name":"可口可乐 330ml",    "price_fen":350,"min_qty":5,"capacity":20}'
post /machines/vm-001/lanes '{"lane_id":"104","sku_id":"coffee-250",   "name":"雀巢咖啡 250ml",    "price_fen":600,"min_qty":3,"capacity":12}'
post /machines/vm-001/lanes '{"lane_id":"105","sku_id":"energy-330",   "name":"红牛 330ml",        "price_fen":700,"min_qty":2,"capacity":12}'

echo ""
echo "Done — seeded at $BASE_URL"
