#!/usr/bin/env bash
# End-to-end test: supply chain → vending machine purchase → pickup
#
# Covers:
#   1. Supply chain baseline (suppliers / SKUs / machines / lanes)
#   2. Place POs for all suppliers
#   3. Advance POs → stocked (inventory rises)
#   4. UCP auth
#   5. Browse catalog — items show qty > 0
#   6. Create checkout session
#   7. Mock Alipay payment + verify
#   8. Complete checkout → order + pickup code
#   9. Inventory decrements after purchase
#  10. Pickup code validation (redeem)
#  11. Sold-out protection (409)
#  12. UCP discovery endpoint
#
# Usage: ./scripts/test-e2e.sh [SC_URL] [UCP_URL]

SC="${1:-https://supply-chain-mock.fxp007.workers.dev}"
UCP="${2:-https://ucp-mock.fxp007.workers.dev}"
MACHINE="vm-001"
PASS=0; FAIL=0

red='\033[0;31m'; green='\033[0;32m'; yellow='\033[1;33m'; reset='\033[0m'; bold='\033[1m'

pass() { echo -e "  ${green}✓${reset} $1"; PASS=$((PASS+1)); }
fail() { echo -e "  ${red}✗${reset} $1"; FAIL=$((FAIL+1)); }
info() { echo -e "  ${yellow}→${reset} $1"; }
section() { echo -e "\n${bold}━━━ $1${reset}"; }

jq_val() { echo "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); print($2)" 2>/dev/null || echo ""; }
jq_int() { echo "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(int($2))" 2>/dev/null || echo "0"; }
url_enc() { python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$1"; }

sc_get()  { curl -sf "$SC$1" 2>/dev/null || echo "{}"; }
sc_post() { curl -sf -X POST "$SC$1" -H "Content-Type: application/json" -d "$2" 2>/dev/null || echo "{}"; }

ucp_post() {
  local path="$1" data="$2" token="${3:-}"
  if [[ -n "$token" ]]; then
    curl -sf -X POST "$UCP$path" -H "Content-Type: application/json" -H "Authorization: Bearer $token" -d "$data" 2>/dev/null || echo "{}"
  else
    curl -sf -X POST "$UCP$path" -H "Content-Type: application/json" -d "$data" 2>/dev/null || echo "{}"
  fi
}
ucp_get() { curl -sf "$UCP$1" -H "Authorization: Bearer $2" 2>/dev/null || echo "{}"; }
ucp_post_status() {
  local path="$1" data="$2" token="${3:-}"
  if [[ -n "$token" ]]; then
    curl -s -o /dev/null -w "%{http_code}" -X POST "$UCP$path" \
      -H "Content-Type: application/json" -H "Authorization: Bearer $token" -d "$data" 2>/dev/null || echo "0"
  else
    curl -s -o /dev/null -w "%{http_code}" -X POST "$UCP$path" \
      -H "Content-Type: application/json" -d "$data" 2>/dev/null || echo "0"
  fi
}

assert_eq() {
  if [[ "$2" == "$3" ]]; then pass "$1"; else fail "$1 (got='$2' want='$3')"; fi
}
assert_ne() {
  if [[ -n "$2" && "$2" != "None" && "$2" != "{}" && "$2" != "" ]]; then pass "$1"; else fail "$1 (empty or None)"; fi
}
assert_gt() {
  if python3 -c "exit(0 if int('${2:-0}') > int('$3') else 1)" 2>/dev/null; then pass "$1"; else fail "$1 (got ${2:-0}, want > $3)"; fi
}

# ── 1. Baseline ───────────────────────────────────────────────────────────────
section "1. Supply chain — baseline"

r=$(sc_get /suppliers)
n=$(jq_int "$r" "len(d)"); assert_gt "有供应商" "$n" "0"; info "供应商: $n"

r=$(sc_get /skus)
n=$(jq_int "$r" "len(d)"); assert_gt "有 SKU" "$n" "0"; info "SKU: $n"

r=$(sc_get /machines)
n=$(jq_int "$r" "len(d)"); assert_gt "有贩卖机" "$n" "0"; info "贩卖机: $n"

r=$(sc_get /machines/$MACHINE/lanes)
n=$(jq_int "$r" "len(d)"); assert_gt "$MACHINE 有货道" "$n" "0"; info "货道: $n"

inv0=$(sc_get /machines/$MACHINE/inventory)
total0=$(echo "$inv0" | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(i['qty'] for i in d))" 2>/dev/null || echo "0")
info "$MACHINE 当前总库存: $total0"

# ── 2. Place POs ──────────────────────────────────────────────────────────────
section "2. 采购 — 向每家供应商下单"

r=$(sc_post /purchase-orders "{\"supplier_id\":\"COKE_CN\",\"machine_id\":\"$MACHINE\",\"items\":[{\"sku_id\":\"CC-001\",\"qty\":48},{\"sku_id\":\"CC-002\",\"qty\":48},{\"sku_id\":\"CC-003\",\"qty\":48}],\"note\":\"e2e\"}")
PO_COKE=$(jq_val "$r" "d['id']"); assert_ne "创建可口可乐 PO" "$PO_COKE"; info "PO: $PO_COKE"

r=$(sc_post /purchase-orders "{\"supplier_id\":\"NONGFU\",\"machine_id\":\"$MACHINE\",\"items\":[{\"sku_id\":\"NF-001\",\"qty\":96},{\"sku_id\":\"NF-002\",\"qty\":24},{\"sku_id\":\"NF-003\",\"qty\":48},{\"sku_id\":\"NF-004\",\"qty\":12}],\"note\":\"e2e\"}")
PO_NF=$(jq_val "$r" "d['id']"); assert_ne "创建农夫山泉 PO" "$PO_NF"; info "PO: $PO_NF"

r=$(sc_post /purchase-orders "{\"supplier_id\":\"NESTLE\",\"machine_id\":\"$MACHINE\",\"items\":[{\"sku_id\":\"NE-001\",\"qty\":24},{\"sku_id\":\"NE-002\",\"qty\":24}],\"note\":\"e2e\"}")
PO_NE=$(jq_val "$r" "d['id']"); assert_ne "创建雀巢 PO" "$PO_NE"; info "PO: $PO_NE"

r=$(sc_post /purchase-orders "{\"supplier_id\":\"MASTER_KONG\",\"machine_id\":\"$MACHINE\",\"items\":[{\"sku_id\":\"MK-001\",\"qty\":48}],\"note\":\"e2e\"}")
PO_MK=$(jq_val "$r" "d['id']"); assert_ne "创建康师傅 PO" "$PO_MK"; info "PO: $PO_MK"

# ── 3. Advance POs → stocked ──────────────────────────────────────────────────
section "3. 入库 — PO 推进到 stocked"

advance_po() {
  local id="$1" label="$2"
  for s in submitted acknowledged shipped received stocked; do
    sc_post "/purchase-orders/$id/advance" "{\"to_status\":\"$s\"}" >/dev/null
  done
  local final
  final=$(jq_val "$(sc_get /purchase-orders/$id)" "d['status']")
  assert_eq "$label → stocked" "$final" "stocked"
}

advance_po "$PO_COKE" "可口可乐 PO"
advance_po "$PO_NF"   "农夫山泉 PO"
advance_po "$PO_NE"   "雀巢 PO"
advance_po "$PO_MK"   "康师傅 PO"

inv1=$(sc_get /machines/$MACHINE/inventory)
total1=$(echo "$inv1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(i['qty'] for i in d))" 2>/dev/null || echo "0")
assert_gt "$MACHINE 入库后库存 > 0" "$total1" "0"; info "入库后总库存: $total1"

# ── 4. UCP auth ───────────────────────────────────────────────────────────────
section "4. UCP 认证"

token_r=$(ucp_post /oauth/token '{"grant_type":"client_credentials","client_id":"demo","client_secret":"demo"}')
TOKEN=$(jq_val "$token_r" "d['access_token']")
assert_ne "获取 access_token" "$TOKEN"; info "Token: ${TOKEN:0:40}…"

# ── 5. Browse catalog ─────────────────────────────────────────────────────────
section "5. 浏览商品目录"

cart_r=$(ucp_post "/cart-sessions?machine_id=$MACHINE" '{}' "$TOKEN")
avail_count=$(echo "$cart_r" | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(1 for i in d.get('items',[]) if i.get('available')))" 2>/dev/null || echo "0")
assert_gt "有可购买商品" "$avail_count" "0"; info "可购买: $avail_count 种"

LANE_ID=$(echo "$cart_r" | python3 -c "
import sys,json
d=json.load(sys.stdin)
avail=[i for i in d.get('items',[]) if i.get('available')]
print(avail[0]['lane_id'] if avail else '')
" 2>/dev/null || echo "")
ITEM_NAME=$(echo "$cart_r" | python3 -c "
import sys,json
d=json.load(sys.stdin)
avail=[i for i in d.get('items',[]) if i.get('available')]
print(avail[0]['name'] if avail else '')
" 2>/dev/null || echo "")
LANE_QTY=$(echo "$cart_r" | python3 -c "
import sys,json
d=json.load(sys.stdin)
avail=[i for i in d.get('items',[]) if i.get('available')]
print(avail[0]['quantity_available'] if avail else 0)
" 2>/dev/null || echo "0")

assert_ne "找到可购买货道 ($LANE_ID)" "$LANE_ID"
info "选择: $ITEM_NAME (lane=$LANE_ID, qty=$LANE_QTY)"

# ── 6. Checkout ───────────────────────────────────────────────────────────────
section "6. 创建结账会话"

cs_r=$(ucp_post "/checkout-sessions?machine_id=$MACHINE" "{\"line_items\":[{\"id\":\"$LANE_ID\"}]}" "$TOKEN")
CS_ID=$(jq_val "$cs_r" "d['checkout_session_id']")
cs_status=$(jq_val "$cs_r" "d['status']")
assert_ne "创建 checkout session" "$CS_ID"
assert_eq "状态 incomplete" "$cs_status" "incomplete"
info "CS: ${CS_ID:0:40}…"

# ── 7. Alipay payment ─────────────────────────────────────────────────────────
section "7. 模拟支付宝付款"

ao_r=$(ucp_post /alipay/create-order \
  "{\"checkout_id\":\"$CS_ID\",\"amount\":300,\"currency\":\"CNY\",\"product_name\":\"$ITEM_NAME\",\"agent_pay_info\":{\"agent_id\":\"e2e-test\"}}" \
  "$TOKEN")
AO_ID=$(jq_val "$ao_r" "d['alipay_order_id']")
assert_ne "创建支付宝订单" "$AO_ID"

pay_r=$(curl -sf -X POST "$UCP/alipay/confirm-payment/$(url_enc "$AO_ID")" \
  -H "Content-Type: application/json" -d '{"auth_method":"face"}' 2>/dev/null || echo "{}")
pay_status=$(jq_val "$pay_r" "d['status']")
INTENT=$(jq_val "$pay_r" "d['intent_credential']")
assert_eq "面容支付成功" "$pay_status" "paid"
assert_ne "获得 intent_credential" "$INTENT"

verify_r=$(ucp_get "/alipay/query-order/$(url_enc "$INTENT")" "$TOKEN")
verify_status=$(jq_val "$verify_r" "d['status']")
assert_eq "验证支付状态 = paid" "$verify_status" "paid"

# ── 8. Complete checkout ──────────────────────────────────────────────────────
section "8. 完成结账 → 生成订单"

complete_r=$(ucp_post "/checkout-sessions/$(url_enc "$CS_ID")/complete?machine_id=$MACHINE" \
  "{\"payment_confirmation\":{\"handler_id\":\"alipay_aipay\",\"intent_credential\":\"$INTENT\"}}" "$TOKEN")
ORDER_ID=$(jq_val "$complete_r" "d['order_id']")
PICKUP_CODE=$(jq_val "$complete_r" "d['pickup_code']")
complete_status=$(jq_val "$complete_r" "d['status']")
assert_ne "生成订单" "$ORDER_ID"
assert_ne "有取货码" "$PICKUP_CODE"
assert_eq "状态 awaiting_pickup" "$complete_status" "awaiting_pickup"
info "Order: ${ORDER_ID:0:40}…"
info "取货码: $PICKUP_CODE"

# ── 9. Inventory decremented ──────────────────────────────────────────────────
section "9. 验证库存已扣减"

inv2=$(sc_get /machines/$MACHINE/inventory)
total2=$(echo "$inv2" | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(i['qty'] for i in d))" 2>/dev/null || echo "$total1")
expected=$((total1 - 1))
assert_eq "总库存减少 1 ($total1 → $expected)" "$total2" "$expected"

# ── 10. Pickup redeem ─────────────────────────────────────────────────────────
section "10. 取货码验证"

redeem_r=$(curl -sf -X POST "$UCP/orders/$(url_enc "$ORDER_ID")/redeem" \
  -H "Content-Type: application/json" -d "{\"pickup_code\":\"$PICKUP_CODE\"}" 2>/dev/null || echo "{}")
redeem_ok=$(jq_val "$redeem_r" "d['ok']")
events_url=$(jq_val "$redeem_r" "d['events_url']")
assert_eq "取货码验证通过" "$redeem_ok" "True"
assert_ne "返回 events_url" "$events_url"
info "Events: $events_url"

# Wrong code
wrong_r=$(curl -sf -X POST "$UCP/orders/$(url_enc "$ORDER_ID")/redeem" \
  -H "Content-Type: application/json" -d '{"pickup_code":"WRONGCODE"}' 2>/dev/null || echo "{}")
wrong_ok=$(jq_val "$wrong_r" "d['ok']")
assert_eq "错误取货码被拒绝" "$wrong_ok" "False"

# ── 11. Sold-out protection ───────────────────────────────────────────────────
section "11. 超卖保护 (409)"

# Get current qty for this lane
lane_qty_now=$(echo "$inv2" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for i in d:
    if i['lane_id']=='$LANE_ID':
        print(i['qty']); break
else:
    print(0)
" 2>/dev/null || echo "0")
info "货道 $LANE_ID 剩余: $lane_qty_now"

sold_out=false
for i in $(seq 1 $((lane_qty_now + 2))); do
  cs2_r=$(ucp_post "/checkout-sessions?machine_id=$MACHINE" "{\"line_items\":[{\"id\":\"$LANE_ID\"}]}" "$TOKEN")
  cs2_id=$(jq_val "$cs2_r" "d['checkout_session_id']")
  if [[ -z "$cs2_id" || "$cs2_id" == "None" ]]; then break; fi
  status=$(ucp_post_status "/checkout-sessions/$(url_enc "$cs2_id")/complete?machine_id=$MACHINE" \
    '{"payment_confirmation":{"handler_id":"prepaid"}}' "$TOKEN")
  if [[ "$status" == "409" ]]; then
    sold_out=true
    pass "售罄时返回 409 (第 $i 次)"
    break
  fi
done
if [[ "$sold_out" == "false" ]]; then fail "未触发 409 售罄保护"; fi

# ── 12. Discovery ─────────────────────────────────────────────────────────────
section "12. UCP discovery"

disc=$(curl -sf "$UCP/.well-known/ucp" 2>/dev/null || echo "{}")
vendor=$(jq_val "$disc" "d['vendor']")
handlers=$(jq_int "$disc" "len(d.get('payment_handlers',[]))")
assert_eq "vendor 正确" "$vendor" "vending-protocol-mock"
assert_gt "payment_handlers ≥ 1" "$handlers" "0"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${bold}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${reset}"
total=$((PASS+FAIL))
if [[ $FAIL -eq 0 ]]; then
  echo -e "${green}${bold}全部通过 ✓  $PASS / $total${reset}"
else
  echo -e "${red}${bold}失败 ✗  $FAIL / $total  (通过 $PASS)${reset}"
fi
echo ""
exit $FAIL
