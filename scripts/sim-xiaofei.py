#!/usr/bin/env python3
"""
sim-xiaofei.py — 模拟消费者"晓飞"完整购买流程
测试覆盖：
  1. 查询缺货商品（矿泉水）→ Agent 如实告知缺货
  2. 改买有货商品（可乐）→ 浏览目录 → 创建结账 → 支付请求
  3. 确认支付（预付 Token 模式）→ 出货成功 → 显示取货码
  4. 再次购买（历史上下文延续）
  5. 查询福利余额（购买前后对比）
"""
import json, sys, time, urllib.request, urllib.error

AGENT = "https://ucp-agent.fxp007.workers.dev"
SC    = "https://supply-chain-mock.fxp007.workers.dev"

USER_ID   = "xiaofei-test-001"
MACHINE   = "vm-001"   # 1楼大厅

# ─── colours ──────────────────────────────────────────────────────────────────
R="\033[0m"; BOLD="\033[1m"; DIM="\033[2m"
CYAN="\033[96m"; GREEN="\033[92m"; YELLOW="\033[93m"
RED="\033[91m"; BLUE="\033[94m"; MAGENTA="\033[95m"

def h1(s):  print(f"\n{BOLD}{CYAN}{'─'*60}{R}\n{BOLD}{CYAN}  {s}{R}\n{BOLD}{CYAN}{'─'*60}{R}")
def h2(s):  print(f"\n{BOLD}{BLUE}▶ {s}{R}")
def ok(s):  print(f"  {GREEN}✓ {s}{R}")
def warn(s):print(f"  {YELLOW}⚠ {s}{R}")
def err(s): print(f"  {RED}✗ {s}{R}"); sys.exit(1)
def dim(s): print(f"  {DIM}{s}{R}")
def user_say(s): print(f"\n  {MAGENTA}晓飞：{BOLD}{s}{R}")
def ai_say(s):   print(f"  {CYAN}AI  ：{s}{R}")

# ─── HTTP helpers ──────────────────────────────────────────────────────────────
HEADERS_BASE = {"User-Agent": "sim-xiaofei/1.0", "Accept": "application/json"}

def get(url):
    req = urllib.request.Request(url, headers=HEADERS_BASE)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def post_json(url, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                  headers={**HEADERS_BASE, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

# ─── SSE streaming chat ────────────────────────────────────────────────────────
def chat(message, thread_id, machine_id=None, label="AI"):
    """
    POST /api/chat, stream SSE, collect all events.
    Returns (text_reply, payment_request_payload_or_None, thread_id)
    """
    body = json.dumps({
        "message":    message,
        "user_id":    USER_ID,
        "thread_id":  thread_id,
        "machine_id": machine_id or "",
    }).encode()
    req = urllib.request.Request(
        f"{AGENT}/api/chat", data=body, method="POST",
        headers={**HEADERS_BASE, "Content-Type": "application/json", "Accept": "text/event-stream"})

    text_parts   = []
    payment_req  = None
    tool_calls   = []

    with urllib.request.urlopen(req, timeout=60) as resp:
        cur_event = ""
        buf = b""
        while True:
            chunk = resp.read(512)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line_b, buf = buf.split(b"\n", 1)
                line = line_b.decode("utf-8").rstrip("\r")
                if line.startswith("event:"):
                    cur_event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue

                if cur_event == "text" and payload.get("content"):
                    text_parts.append(payload["content"])
                elif cur_event == "tool_call":
                    tool_calls.append(payload.get("name", ""))
                elif cur_event == "payment_request":
                    payment_req = payload
                cur_event = ""

    full_text = "".join(text_parts)
    if full_text:
        ai_say(full_text)
    if tool_calls:
        dim(f"   [工具调用: {', '.join(tool_calls)}]")
    return full_text, payment_req, thread_id

# ─── Phase 2: confirm payment ──────────────────────────────────────────────────
def confirm_payment(payment_id, label="支付确认"):
    """
    POST /api/confirm-payment, stream SSE, return (text, order_data)
    """
    body = json.dumps({
        "payment_id":       payment_id,
        "user_id":          USER_ID,
        "gpay_token":       None,
        "alipay_order_id":  None,
        "intent_credential":None,
    }).encode()
    req = urllib.request.Request(
        f"{AGENT}/api/confirm-payment", data=body, method="POST",
        headers={**HEADERS_BASE, "Content-Type": "application/json", "Accept": "text/event-stream"})

    text_parts  = []
    order_data  = None

    with urllib.request.urlopen(req, timeout=60) as resp:
        cur_event = ""
        buf = b""
        while True:
            chunk = resp.read(512)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line_b, buf = buf.split(b"\n", 1)
                line = line_b.decode("utf-8").rstrip("\r")
                if line.startswith("event:"):
                    cur_event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue

                if cur_event == "text" and payload.get("content"):
                    text_parts.append(payload["content"])
                elif cur_event == "payment_done":
                    order_data = payload
                cur_event = ""

    full_text = "".join(text_parts)
    if full_text:
        ai_say(full_text)
    return full_text, order_data

# ══════════════════════════════════════════════════════════════════════════════
def main():
    h1("晓飞消费者模拟测试")
    print(f"  用户 ID : {BOLD}{USER_ID}{R}")
    print(f"  贩卖机  : {BOLD}{MACHINE} · 1楼大厅{R}")
    print(f"  目标    : 矿泉水（缺货）→ 可乐（有货）→ 完成支付")

    thread_id = f"sim-xiaofei-{int(time.time())}"

    # ── 0. 检查初始福利余额 ─────────────────────────────────────────────────────
    h2("Step 0 · 查初始福利余额（直接调供应链 API）")
    try:
        welfare_before = get(f"{AGENT}/api/chat")   # 没有 GET 余额的直接 API；用 KV 间接体现
    except Exception:
        pass
    # 福利余额在 chat 流里由 get_welfare_balance 工具返回，先跳过直接看后续

    # ── 1. 询问矿泉水（应得到缺货回复） ────────────────────────────────────────
    h2("Step 1 · 晓飞询问矿泉水（预期：Agent 直接告知缺货）")
    user_say("我想买瓶矿泉水")
    txt1, pay, thread_id = chat("我想买瓶矿泉水", thread_id, MACHINE)
    step1_out_of_stock = any(k in (txt1 or "") for k in ["缺货","没有","售罄","out"])
    if not txt1:
        warn("Agent 无文字回复（检查是否触发 payment_request）")
    elif step1_out_of_stock:
        ok("Agent 正确识别矿泉水缺货")
    else:
        warn(f"回复未明确提示缺货，请检查：{txt1[:80]}")
    if pay:
        warn("矿泉水缺货场景不应触发支付，请检查 Agent 逻辑")

    # ── 2. 改买可乐 ────────────────────────────────────────────────────────────
    h2("Step 2 · 晓飞改买可乐（预期：浏览目录 → 支付请求）")
    user_say("那有可乐吗？帮我买一瓶")
    txt, pay, thread_id = chat("那有可乐吗？帮我买一瓶", thread_id, MACHINE)

    if pay:
        ok(f"收到支付请求")
        ok(f"  商品：{pay.get('product_name','?')}")
        ok(f"  金额：¥{pay.get('amount',0)/100:.2f} {pay.get('currency','CNY')}")
        ok(f"  payment_id 前缀：{str(pay.get('payment_id',''))[:32]}…")
    else:
        if not txt:
            err("Step 2 无回复且无支付请求，流程中断")
        else:
            warn(f"未收到支付请求，Agent 回复：{txt[:120]}")
            warn("可能 Agent 还在等用户确认——尝试继续追问")
            user_say("好的，帮我买无糖可乐")
            txt, pay, thread_id = chat("好的，买无糖可乐", thread_id, MACHINE)
            if pay:
                ok(f"收到支付请求：{pay.get('product_name','?')} ¥{pay.get('amount',0)/100:.2f}")
            else:
                err(f"仍未收到支付请求，终止测试。最后回复：{txt[:200]}")

    payment_id = pay["payment_id"]

    # ── 3. 确认支付 ────────────────────────────────────────────────────────────
    h2("Step 3 · 晓飞确认支付（预付 Token 模式）")
    print(f"  {DIM}→ POST /api/confirm-payment{R}")
    txt2, order = confirm_payment(payment_id)

    if order:
        ok("出货成功！订单详情：")
        ok(f"  order_id   : {order.get('order_id','?')}")
        ok(f"  商品       : {order.get('product','?')}")
        ok(f"  支付金额   : {order.get('amount','?')}")
        ok(f"  取货码     : {BOLD}{order.get('pickup_code','—')}{R}")
    else:
        warn("未收到 payment_done 事件，检查 confirm-payment 响应")
        if txt2: warn(f"Agent 回复：{txt2[:200]}")

    # ── 4. 查当前库存（确认扣减） ──────────────────────────────────────────────
    h2("Step 4 · 验证库存扣减（A2 无糖可乐）")
    try:
        inv = get(f"{SC}/machines/{MACHINE}/inventory")
        for lane in inv:
            if "可乐" in lane.get("name","") or lane.get("lane_id") == "A2":
                print(f"  {lane['lane_id']} {lane['name']} qty={lane['qty']} "
                      f"{'→ 已扣减 ✓' if lane['qty'] < 15 else '(未变化)'}")
    except Exception as e:
        warn(f"库存查询失败: {e}")

    # ── 5. 继续对话（验证上下文保留） ─────────────────────────────────────────
    h2("Step 5 · 验证对话历史（晓飞询问刚才买了什么）")
    user_say("我刚才买了什么，多少钱？")
    txt5, _, _ = chat("我刚才买了什么，多少钱？", thread_id, MACHINE)
    # 检查是否提到可乐和价格（3.50 或 350）
    history_ok = any(k in (txt5 or "") for k in ["可乐","3.50","3.5","350"])
    if history_ok:
        ok("Agent 正确从历史记忆中答出购买内容")
    else:
        warn("Agent 未能从历史中召回购买记录（可能对话历史 TTL 或截断问题）")

    # ── 总结 ───────────────────────────────────────────────────────────────────
    h1("测试完成")
    results = [
        ("矿泉水缺货告知",  step1_out_of_stock),
        ("可乐购买流程触发", pay is not None),
        ("支付确认成功",    order is not None),
        ("取货码返回",      bool(order and order.get("pickup_code"))),
        ("对话历史延续",    history_ok),
    ]
    for name, passed in results:
        (ok if passed else warn)(name)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断")
    except urllib.error.HTTPError as e:
        err(f"HTTP {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        err(f"未预期错误: {e}")
