#!/usr/bin/env bash
# Seed initial suppliers, SKUs, machines, and lanes.
# Usage: ./scripts/seed.sh [BASE_URL]
# Default BASE_URL: https://supply-chain-mock.fxp007.workers.dev

set -euo pipefail

BASE="${1:-https://supply-chain-mock.fxp007.workers.dev}"
OK=0; FAIL=0

post() {
  local label="$1" path="$2" data="$3"
  local resp status
  resp=$(curl -s -w "\n%{http_code}" -X POST "$BASE$path" \
    -H "Content-Type: application/json" -d "$data")
  status=$(echo "$resp" | tail -1)
  body=$(echo "$resp" | head -n -1)
  if [[ "$status" == "200" || "$status" == "201" ]]; then
    echo "  ✓ $label"
    OK=$((OK+1))
  else
    echo "  ✗ $label  [HTTP $status]  $(echo "$body" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("error",""))' 2>/dev/null)"
    FAIL=$((FAIL+1))
  fi
}

echo ""
echo "━━━ Suppliers ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
post "可口可乐公司"   /suppliers '{"id":"COKE_CN","name":"可口可乐公司","lead_time_days":2,"min_order_yuan":500}'
post "农夫山泉"       /suppliers '{"id":"NONGFU","name":"农夫山泉股份有限公司","lead_time_days":1,"min_order_yuan":300}'
post "康师傅控股"     /suppliers '{"id":"MASTER_KONG","name":"康师傅控股有限公司","lead_time_days":2,"min_order_yuan":600}'
post "雀巢中国"       /suppliers '{"id":"NESTLE","name":"雀巢（中国）有限公司","lead_time_days":3,"min_order_yuan":800}'

echo ""
echo "━━━ SKUs ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
# 可口可乐
post "无糖可乐 330ml"   /skus '{"sku_id":"CC-001","name":"无糖可乐 330ml","cost_fen":180,"retail_fen":350,"moq":24,"supplier_id":"COKE_CN"}'
post "经典可乐 330ml"   /skus '{"sku_id":"CC-002","name":"经典可乐 330ml","cost_fen":160,"retail_fen":300,"moq":24,"supplier_id":"COKE_CN"}'
post "雪碧 330ml"       /skus '{"sku_id":"CC-003","name":"雪碧 330ml","cost_fen":160,"retail_fen":300,"moq":24,"supplier_id":"COKE_CN"}'
# 农夫山泉
post "矿泉水 550ml"     /skus '{"sku_id":"NF-001","name":"矿泉水 550ml","cost_fen":80,"retail_fen":200,"moq":48,"supplier_id":"NONGFU"}'
post "东方树叶绿茶 500ml" /skus '{"sku_id":"NF-002","name":"东方树叶绿茶 500ml","cost_fen":220,"retail_fen":500,"moq":12,"supplier_id":"NONGFU"}'
post "苏打气泡水 330ml"  /skus '{"sku_id":"NF-003","name":"苏打气泡水 330ml","cost_fen":200,"retail_fen":400,"moq":24,"supplier_id":"NONGFU"}'
post "NFC橙汁 900ml"    /skus '{"sku_id":"NF-004","name":"NFC橙汁 900ml","cost_fen":950,"retail_fen":1800,"moq":6,"supplier_id":"NONGFU"}'
# 康师傅
post "冰红茶 500ml"     /skus '{"sku_id":"MK-001","name":"冰红茶 500ml","cost_fen":160,"retail_fen":350,"moq":24,"supplier_id":"MASTER_KONG"}'
post "矿物质水 550ml"   /skus '{"sku_id":"MK-002","name":"矿物质水 550ml","cost_fen":75,"retail_fen":200,"moq":48,"supplier_id":"MASTER_KONG"}'
# 雀巢
post "雀巢拿铁咖啡 268ml" /skus '{"sku_id":"NE-001","name":"雀巢拿铁咖啡 268ml","cost_fen":600,"retail_fen":1200,"moq":12,"supplier_id":"NESTLE"}'
post "雀巢美式黑咖啡 268ml" /skus '{"sku_id":"NE-002","name":"雀巢美式黑咖啡 268ml","cost_fen":600,"retail_fen":1200,"moq":12,"supplier_id":"NESTLE"}'

echo ""
echo "━━━ Machines ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
post "vm-001 1楼大厅"     /machines '{"id":"vm-001","name":"1楼大厅","location":"1F Main Hall"}'
post "vm-002 2楼会议区"   /machines '{"id":"vm-002","name":"2楼会议区","location":"2F Conference Wing"}'
post "vm-003 地下停车场"  /machines '{"id":"vm-003","name":"地下停车场","location":"B1 Parking"}'

echo ""
echo "━━━ Lanes: vm-001 (1楼大厅) ━━━━━━━━━━━━━━━━━━━━━━━━━━━"
post "A1 矿泉水"          /machines/vm-001/lanes '{"lane_id":"A1","sku_id":"NF-001","name":"矿泉水 550ml","price_fen":200,"currency":"CNY","min_qty":5,"capacity":20,"location":"A1"}'
post "A2 无糖可乐"        /machines/vm-001/lanes '{"lane_id":"A2","sku_id":"CC-001","name":"无糖可乐 330ml","price_fen":350,"currency":"CNY","min_qty":5,"capacity":15,"location":"A2"}'
post "A3 经典可乐"        /machines/vm-001/lanes '{"lane_id":"A3","sku_id":"CC-002","name":"经典可乐 330ml","price_fen":300,"currency":"CNY","min_qty":5,"capacity":15,"location":"A3"}'
post "B1 雪碧"            /machines/vm-001/lanes '{"lane_id":"B1","sku_id":"CC-003","name":"雪碧 330ml","price_fen":300,"currency":"CNY","min_qty":5,"capacity":15,"location":"B1"}'
post "B2 绿茶"            /machines/vm-001/lanes '{"lane_id":"B2","sku_id":"NF-002","name":"东方树叶绿茶 500ml","price_fen":500,"currency":"CNY","min_qty":4,"capacity":12,"location":"B2"}'
post "B3 气泡水"          /machines/vm-001/lanes '{"lane_id":"B3","sku_id":"NF-003","name":"苏打气泡水 330ml","price_fen":400,"currency":"CNY","min_qty":4,"capacity":15,"location":"B3"}'
post "C1 冰红茶"          /machines/vm-001/lanes '{"lane_id":"C1","sku_id":"MK-001","name":"冰红茶 500ml","price_fen":350,"currency":"CNY","min_qty":5,"capacity":15,"location":"C1"}'
post "C2 拿铁咖啡"        /machines/vm-001/lanes '{"lane_id":"C2","sku_id":"NE-001","name":"雀巢拿铁咖啡 268ml","price_fen":1200,"currency":"CNY","min_qty":3,"capacity":10,"location":"C2"}'
post "C3 美式黑咖啡"      /machines/vm-001/lanes '{"lane_id":"C3","sku_id":"NE-002","name":"雀巢美式黑咖啡 268ml","price_fen":1200,"currency":"CNY","min_qty":3,"capacity":10,"location":"C3"}'
post "D1 NFC橙汁"         /machines/vm-001/lanes '{"lane_id":"D1","sku_id":"NF-004","name":"NFC橙汁 900ml","price_fen":1800,"currency":"CNY","min_qty":2,"capacity":8,"location":"D1"}'

echo ""
echo "━━━ Lanes: vm-002 (2楼会议区) ━━━━━━━━━━━━━━━━━━━━━━━━━"
post "A1 矿物质水"        /machines/vm-002/lanes '{"lane_id":"A1","sku_id":"MK-002","name":"矿物质水 550ml","price_fen":200,"currency":"CNY","min_qty":5,"capacity":20,"location":"A1"}'
post "A2 无糖可乐"        /machines/vm-002/lanes '{"lane_id":"A2","sku_id":"CC-001","name":"无糖可乐 330ml","price_fen":350,"currency":"CNY","min_qty":5,"capacity":15,"location":"A2"}'
post "B1 绿茶"            /machines/vm-002/lanes '{"lane_id":"B1","sku_id":"NF-002","name":"东方树叶绿茶 500ml","price_fen":500,"currency":"CNY","min_qty":4,"capacity":12,"location":"B1"}'
post "B2 拿铁咖啡"        /machines/vm-002/lanes '{"lane_id":"B2","sku_id":"NE-001","name":"雀巢拿铁咖啡 268ml","price_fen":1200,"currency":"CNY","min_qty":3,"capacity":10,"location":"B2"}'
post "B3 美式黑咖啡"      /machines/vm-002/lanes '{"lane_id":"B3","sku_id":"NE-002","name":"雀巢美式黑咖啡 268ml","price_fen":1200,"currency":"CNY","min_qty":3,"capacity":10,"location":"B3"}'
post "C1 气泡水"          /machines/vm-002/lanes '{"lane_id":"C1","sku_id":"NF-003","name":"苏打气泡水 330ml","price_fen":400,"currency":"CNY","min_qty":4,"capacity":15,"location":"C1"}'

echo ""
echo "━━━ Lanes: vm-003 (地下停车场) ━━━━━━━━━━━━━━━━━━━━━━━━━"
post "A1 矿泉水"          /machines/vm-003/lanes '{"lane_id":"A1","sku_id":"NF-001","name":"矿泉水 550ml","price_fen":200,"currency":"CNY","min_qty":5,"capacity":20,"location":"A1"}'
post "A2 经典可乐"        /machines/vm-003/lanes '{"lane_id":"A2","sku_id":"CC-002","name":"经典可乐 330ml","price_fen":300,"currency":"CNY","min_qty":5,"capacity":15,"location":"A2"}'
post "A3 雪碧"            /machines/vm-003/lanes '{"lane_id":"A3","sku_id":"CC-003","name":"雪碧 330ml","price_fen":300,"currency":"CNY","min_qty":5,"capacity":15,"location":"A3"}'
post "B1 冰红茶"          /machines/vm-003/lanes '{"lane_id":"B1","sku_id":"MK-001","name":"冰红茶 500ml","price_fen":350,"currency":"CNY","min_qty":4,"capacity":12,"location":"B1"}'

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "完成：✓ $OK 成功  ✗ $FAIL 失败"
echo ""
