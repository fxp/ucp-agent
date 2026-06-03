/**
 * UCP Agent Worker — full-featured UCP AI shopping assistant.
 * Fixes: stateless JWT payment_id (no cross-isolate _pending Map).
 * New:   welfare (KV), supply-chain (Service Binding), Slack, biometric AP2.
 */

export interface Env {
  GLM_API_KEY: string;
  AGENT_SECRET: string;       // signs payment JWTs; set via wrangler secret
  MERCHANT_URL: string;
  SLACK_BOT_TOKEN: string;    // optional; leave empty to mock-log
  SLACK_OPS_CHANNEL: string;  // default #vending-ops
  ASSETS: Fetcher;
  MOCK: Fetcher;
  SUPPLY_CHAIN: Fetcher;
  WELFARE_KV: KVNamespace;
}

const GLM_URL   = "https://open.bigmodel.cn/api/paas/v4/chat/completions";
const GLM_MODEL = "glm-4-flash";
const DEFAULT_WELFARE_YUAN = 50.0;
const PAYMENT_JWT_TTL = 3600; // 1 hour

// ── JWT helpers (HMAC-SHA256, same pattern as mock worker) ────────────────────
async function hmacSign(data: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
  return btoa(String.fromCharCode(...new Uint8Array(sig)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

function b64url(obj: object): string {
  return btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

async function signJWT(payload: object, secret: string): Promise<string> {
  const h = b64url({ alg: "HS256", typ: "JWT" });
  const p = b64url({ ...payload, iat: Math.floor(Date.now() / 1000) });
  const sig = await hmacSign(`${h}.${p}`, secret);
  return `${h}.${p}.${sig}`;
}

async function verifyJWT(token: string, secret: string): Promise<Record<string, unknown> | null> {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const expected = await hmacSign(`${parts[0]}.${parts[1]}`, secret);
  if (expected !== parts[2]) return null;
  try {
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"))) as Record<string, unknown>;
    if (payload.exp && Number(payload.exp) < Math.floor(Date.now() / 1000)) return null;
    return payload;
  } catch { return null; }
}

// ── AP2 mandate builder ────────────────────────────────────────────────────────
function buildMandate(body: object): string {
  const h = b64url({ alg: "ES256", typ: "ap2+sd-jwt", kid: "ucp-agent-v1" });
  const p = b64url(body);
  return `${h}.${p}.mock_platform_sig`;
}

// ── HTTP helpers ──────────────────────────────────────────────────────────────
const enc = new TextEncoder();
function sseEnc(event: string, data: object): Uint8Array {
  return enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}
function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" } });
}
function agentBase(url: URL): string { return `${url.protocol}//${url.host}`; }
function ucpHeaders(base: string, token?: string): Record<string, string> {
  const h: Record<string, string> = {
    "Content-Type": "application/json",
    "UCP-Agent": `profile="${base}/profile"`,
    "request-id": crypto.randomUUID(),
  };
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}

async function mockGet(mock: Fetcher, path: string, base: string, token?: string) {
  const r = await mock.fetch(new Request(`https://mock${path}`, { headers: ucpHeaders(base, token) }));
  return { status: r.status, data: await r.json().catch(() => ({})) };
}
async function mockPost(mock: Fetcher, path: string, base: string, body?: unknown, token?: string, form?: string) {
  if (form !== undefined) {
    const r = await mock.fetch(new Request(`https://mock${path}`, { method: "POST", body: form,
      headers: { "Content-Type": "application/x-www-form-urlencoded", "UCP-Agent": `profile="${base}/profile"` } }));
    return { status: r.status, data: await r.json().catch(() => ({})) };
  }
  const r = await mock.fetch(new Request(`https://mock${path}`, { method: "POST",
    body: body !== undefined ? JSON.stringify(body) : undefined, headers: ucpHeaders(base, token) }));
  return { status: r.status, data: await r.json().catch(() => ({})) };
}

async function scGet(sc: Fetcher, path: string, params: Record<string, string> = {}) {
  const u = new URL(`https://sc${path}`);
  for (const [k, v] of Object.entries(params)) if (v) u.searchParams.set(k, v);
  const r = await sc.fetch(new Request(u.toString(), { headers: { "Content-Type": "application/json" } }));
  return { status: r.status, data: await r.json().catch(() => ({})) };
}
async function scPost(sc: Fetcher, path: string, body: unknown) {
  const r = await sc.fetch(new Request(`https://sc${path}`, { method: "POST",
    body: JSON.stringify(body), headers: { "Content-Type": "application/json" } }));
  return { status: r.status, data: await r.json().catch(() => ({})) };
}

// ── Welfare KV ────────────────────────────────────────────────────────────────
async function getWelfare(kv: KVNamespace, userId: string) {
  const raw = await kv.get(`welfare:${userId}`);
  return raw ? JSON.parse(raw) : { quota: DEFAULT_WELFARE_YUAN, used: 0.0, remaining: DEFAULT_WELFARE_YUAN };
}
async function putWelfare(kv: KVNamespace, userId: string, w: object) {
  await kv.put(`welfare:${userId}`, JSON.stringify(w));
}

// ── Optional Slack notify ─────────────────────────────────────────────────────
async function slackPost(token: string, channel: string, text: string): Promise<void> {
  if (!token) { console.log(`[Slack mock] ${channel}: ${text}`); return; }
  await fetch("https://slack.com/api/chat.postMessage", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ channel, text }),
  });
}

// ── Mock supplier SKUs (inline, no external call needed) ──────────────────────
const MOCK_SKUS = [
  { sku_id: "YY-001", name: "无糖可乐 330ml",    brand: "可口可乐", price_cents: 220, eta_days: 2 },
  { sku_id: "YY-002", name: "东方树叶绿茶 500ml", brand: "农夫山泉", price_cents: 320, eta_days: 2 },
  { sku_id: "YY-003", name: "苏打气泡水 330ml",   brand: "农夫山泉", price_cents: 380, eta_days: 3 },
  { sku_id: "YY-004", name: "雀巢拿铁咖啡 268ml","brand": "雀巢",    price_cents: 600, eta_days: 3 },
  { sku_id: "YY-005", name: "矿泉水 550ml",       brand: "农夫山泉", price_cents: 150, eta_days: 1 },
  { sku_id: "YY-006", name: "茉莉蜜茶 500ml",     brand: "康师傅",  price_cents: 160, eta_days: 2 },
];

// ── Tool schemas ───────────────────────────────────────────────────────────────
const TOOLS = [
  { type: "function", function: { name: "ucp_discover",
    description: "GET /.well-known/ucp — 发现商户能力和支付处理器。",
    parameters: { type: "object", properties: {}, required: [] }}},
  { type: "function", function: { name: "ucp_get_token",
    description: "POST /oauth/token — 获取 OAuth 2.0 Bearer Token。",
    parameters: { type: "object", properties: {}, required: [] }}},
  { type: "function", function: { name: "ucp_browse_catalog",
    description: "POST /cart-sessions — 浏览商品目录，含 lane_id、价格、库存。",
    parameters: { type: "object", properties: { token: { type: "string" }}, required: ["token"] }}},
  { type: "function", function: { name: "ucp_create_checkout",
    description: "POST /checkout-sessions — 创建结账会话，返回 checkout_id 和价格。",
    parameters: { type: "object",
      properties: { token: { type: "string" }, lane_id: { type: "string" }, buyer_email: { type: "string" }},
      required: ["token", "lane_id", "buyer_email"] }}},
  { type: "function", function: { name: "ucp_request_payment",
    description: "发起支付请求（流程暂停等用户确认）。amount 单位为分。",
    parameters: { type: "object",
      properties: {
        token: { type: "string" }, checkout_id: { type: "string" },
        amount: { type: "integer" }, currency: { type: "string" },
        product_name: { type: "string" }, lane_id: { type: "string" },
      },
      required: ["token", "checkout_id", "amount", "currency", "product_name", "lane_id"] }}},
  { type: "function", function: { name: "get_welfare_balance",
    description: "查询员工企业福利余额。购物前调用，余额>0 时提示可抵扣。",
    parameters: { type: "object", properties: { user_id: { type: "string" }}, required: ["user_id"] }}},
  { type: "function", function: { name: "query_supplier_sku",
    description: "货柜缺货时查询供应商 SKU 目录（用友宝）。",
    parameters: { type: "object", properties: { keyword: { type: "string" }}, required: ["keyword"] }}},
  { type: "function", function: { name: "create_preorder",
    description: "为缺货商品创建用户预订单，等待补货后通知。",
    parameters: { type: "object",
      properties: { sku_id: { type: "string" }, sku_name: { type: "string" },
        quantity: { type: "integer" }, user_id: { type: "string" }, note: { type: "string" }},
      required: ["sku_id", "sku_name", "quantity", "user_id"] }}},
  { type: "function", function: { name: "get_inventory",
    description: "查询贩卖机各货道当前库存。low_stock_only=true 只返回需补货货道。",
    parameters: { type: "object",
      properties: { low_stock_only: { type: "boolean" }},
      required: [] }}},
  { type: "function", function: { name: "preview_daily_order",
    description: "预览今日自动补货采购计划（不实际提交）。",
    parameters: { type: "object", properties: {}, required: [] }}},
  { type: "function", function: { name: "trigger_daily_order",
    description: "执行每日自动补货：汇总低库存+预订单 → 提交采购单。dry_run=true 仅预览。",
    parameters: { type: "object",
      properties: { dry_run: { type: "boolean" }},
      required: [] }}},
  { type: "function", function: { name: "notify_ops",
    description: "发送运营通知到 Slack #vending-ops。",
    parameters: { type: "object", properties: { message: { type: "string" }}, required: ["message"] }}},
];

// ── Tool execution ─────────────────────────────────────────────────────────────
async function execTool(
  name: string, args: Record<string, unknown>,
  mock: Fetcher, sc: Fetcher, kv: KVNamespace,
  base: string, secret: string, env: Env,
): Promise<{ status: number; data: unknown }> {
  switch (name) {

    case "ucp_discover":
      return mockGet(mock, "/.well-known/ucp", base);

    case "ucp_get_token":
      return mockPost(mock, "/oauth/token", base, undefined, undefined,
        "grant_type=client_credentials&client_id=demo&client_secret=demo");

    case "ucp_browse_catalog":
      return mockPost(mock, "/cart-sessions", base, {}, args.token as string);

    case "ucp_create_checkout":
      return mockPost(mock, "/checkout-sessions", base, {
        line_items: [{ id: args.lane_id, quantity: 1 }],
        buyer: { email: args.buyer_email ?? "ai-agent@vending.local" },
        payment: { handler_id: "alipay_aipay", instrument: {} },
      }, args.token as string);

    case "ucp_request_payment": {
      let alipayConfig: unknown = null, gpayConfig: unknown = null;
      try {
        const profile = await mockGet(mock, "/.well-known/ucp", base);
        const handlers = ((profile.data as Record<string, unknown>)?.payment_handlers ?? []) as Array<Record<string, unknown>>;
        for (const h of handlers) {
          if (h.handler_id === "alipay_aipay") alipayConfig = h.config ?? h.ap2;
          if (h.handler_id === "google_pay")   gpayConfig   = h.config;
        }
      } catch { /* ignore */ }
      const paymentJwt = await signJWT({
        sub: "pending_payment",
        token: args.token, checkout_id: args.checkout_id,
        amount: args.amount, currency: args.currency,
        product_name: args.product_name, lane_id: args.lane_id,
        exp: Math.floor(Date.now() / 1000) + PAYMENT_JWT_TTL,
      }, secret);
      return { status: 200, data: {
        payment_id: paymentJwt, requires_user_confirmation: true,
        amount: args.amount, currency: args.currency, product_name: args.product_name,
        google_pay: gpayConfig, alipay: alipayConfig,
        message: "Payment authorization required. Waiting for user to confirm.",
      }};
    }

    case "get_welfare_balance": {
      const uid = String(args.user_id || "guest");
      const w = await getWelfare(kv, uid);
      return { status: 200, data: {
        user_id: uid, quota_yuan: w.quota, used_yuan: w.used, remaining_yuan: w.remaining,
        currency: "CNY",
        hint: w.remaining > 0 ? `企业福利余额 ¥${w.remaining.toFixed(2)}，可抵扣消费` : "福利余额已用完",
      }};
    }

    case "query_supplier_sku": {
      const kw = String(args.keyword ?? "").toLowerCase();
      const matches = MOCK_SKUS.filter(s =>
        s.name.toLowerCase().includes(kw) || s.brand.toLowerCase().includes(kw)
      );
      return { status: 200, data: {
        source: "mock",
        keyword: args.keyword,
        results: (matches.length ? matches : MOCK_SKUS.slice(0, 3)).map(s => ({
          ...s, price_yuan: `¥${(s.price_cents / 100).toFixed(2)}`,
          eta: `${s.eta_days} 天内到货`,
        })),
      }};
    }

    case "create_preorder":
      return scPost(sc, "/preorders", {
        sku_id: args.sku_id, sku_name: args.sku_name, qty: args.quantity ?? 1,
        user_id: args.user_id ?? "guest", note: args.note ?? "",
        notify_channel: "slack",
      });

    case "get_inventory":
      return scGet(sc, "/inventory", { low_stock_only: args.low_stock_only ? "true" : "" });

    case "preview_daily_order":
      return scGet(sc, "/internal/daily-order/preview");

    case "trigger_daily_order":
      return scPost(sc, "/internal/daily-order", { dry_run: args.dry_run ?? false });

    case "notify_ops": {
      const ch = env.SLACK_OPS_CHANNEL || "#vending-ops";
      await slackPost(env.SLACK_BOT_TOKEN, ch, String(args.message ?? ""));
      return { status: 200, data: { ok: true, channel: ch, mock: !env.SLACK_BOT_TOKEN }};
    }

    default:
      return { status: 400, data: { error: `unknown tool: ${name}` }};
  }
}

// ── Phase 1: chat ─────────────────────────────────────────────────────────────
function phase1Stream(
  userMessage: string, userId: string,
  mock: Fetcher, sc: Fetcher, kv: KVNamespace,
  base: string, env: Env,
): ReadableStream {
  const systemPrompt =
    `你是 UCP Vending Agent，AI 驱动的自动贩卖机智能购物助手。\n` +
    `Agent Profile: ${base}/profile    当前用户: ${userId}\n\n` +
    `标准购买流程（严格按序）：\n` +
    `1. ucp_discover  2. ucp_get_token  3. get_welfare_balance(user_id="${userId}")\n` +
    `4. ucp_browse_catalog  5. ucp_create_checkout  6. ucp_request_payment\n` +
    `   ⚠️ ucp_request_payment 调用后停止，等待用户支付，不要调用 complete。\n\n` +
    `缺货：先 query_supplier_sku，询问预订 → create_preorder → notify_ops。\n` +
    `供应链查询：get_inventory / preview_daily_order / trigger_daily_order。\n` +
    `价格用 ¥X.XX 格式，回复简洁。`;

  const messages: unknown[] = [
    { role: "system", content: systemPrompt },
    { role: "user",   content: userMessage },
  ];

  return new ReadableStream({
    async start(ctrl) {
      try {
        for (let i = 0; i < 10; i++) {
          const res = await fetch(GLM_URL, {
            method: "POST",
            headers: { "Authorization": `Bearer ${env.GLM_API_KEY}`, "Content-Type": "application/json" },
            body: JSON.stringify({ model: GLM_MODEL, messages, tools: TOOLS, tool_choice: "auto", stream: false, max_tokens: 1000 }),
          });
          if (!res.ok) {
            ctrl.enqueue(sseEnc("text", { content: `❌ GLM API error: ${res.status}` })); break;
          }
          const result = await res.json() as {
            choices: Array<{ finish_reason: string; message: {
              content?: string;
              tool_calls?: Array<{ id: string; function: { name: string; arguments: string } }>;
            }}>;
          };
          const msg    = result.choices[0].message;
          const finish = result.choices[0].finish_reason;

          if (msg.content) ctrl.enqueue(sseEnc("text", { content: msg.content }));
          if (finish === "stop" || !msg.tool_calls?.length) break;

          messages.push({
            role: "assistant", content: msg.content ?? "",
            tool_calls: msg.tool_calls.map(tc => ({
              id: tc.id, type: "function",
              function: { name: tc.function.name, arguments: tc.function.arguments },
            })),
          });

          for (const tc of msg.tool_calls) {
            const fn = tc.function.name;
            let args: Record<string, unknown> = {};
            try { args = JSON.parse(tc.function.arguments || "{}"); } catch { /* ignore */ }

            ctrl.enqueue(sseEnc("tool_call", { id: tc.id, name: fn, args }));
            const toolRes = await execTool(fn, args, mock, sc, kv, base, env.AGENT_SECRET, env);
            ctrl.enqueue(sseEnc("tool_result", { id: tc.id, name: fn, status: toolRes.status, data: toolRes.data }));
            messages.push({ role: "tool", tool_call_id: tc.id, content: JSON.stringify(toolRes) });

            if (fn === "ucp_request_payment") {
              const d = toolRes.data as Record<string, unknown>;
              if (d?.requires_user_confirmation) {
                ctrl.enqueue(sseEnc("payment_request", {
                  payment_id: d.payment_id,
                  checkout_id: args.checkout_id ?? "",
                  token: args.token ?? "",
                  amount: d.amount, currency: d.currency,
                  product_name: d.product_name,
                  google_pay: d.google_pay, alipay: d.alipay,
                  user_id: userId,
                }));
              }
            }
          }
        }
      } catch (e) {
        console.error("phase1", e);
        ctrl.enqueue(sseEnc("text", { content: "❌ Agent 异常，请重试。" }));
      }
      ctrl.enqueue(sseEnc("done", {}));
      ctrl.close();
    },
  });
}

// ── Phase 2: confirm payment ───────────────────────────────────────────────────
function phase2Stream(
  paymentId: string, gpayToken: string | null,
  alipayOrderId: string | null, intentCredential: string | null,
  userId: string, biometric: Record<string, unknown> | null,
  mock: Fetcher, sc: Fetcher, kv: KVNamespace,
  base: string, env: Env,
): ReadableStream {
  return new ReadableStream({
    async start(ctrl) {
      try {
        // Decode stateless JWT payment_id (no Map lookup needed)
        const pending = await verifyJWT(paymentId, env.AGENT_SECRET);
        if (!pending) {
          ctrl.enqueue(sseEnc("text", { content: "⚠️ 支付链接已过期，请重新发起购买。" }));
          ctrl.enqueue(sseEnc("done", {})); ctrl.close(); return;
        }
        const { token, checkout_id, amount, currency, product_name: product } =
          pending as Record<string, string | number>;

        // Server-side Alipay verification
        if (alipayOrderId) {
          const vid = `av-${crypto.randomUUID().slice(0, 8)}`;
          ctrl.enqueue(sseEnc("tool_call", { id: vid, name: "alipay_verify_payment",
            args: { alipay_order_id: alipayOrderId, checkout_id } }));
          const verify = await mockGet(mock, `/alipay/query-order/${encodeURIComponent(alipayOrderId)}`, base, String(token));
          ctrl.enqueue(sseEnc("tool_result", { id: vid, name: "alipay_verify_payment",
            status: verify.status, data: verify.data }));
          const vdata = verify.data as Record<string, unknown>;
          if (vdata.checkout_id && vdata.checkout_id !== checkout_id) {
            ctrl.enqueue(sseEnc("text", { content: "⚠️ 支付订单与结账会话不匹配，拒绝继续。" }));
            ctrl.enqueue(sseEnc("done", {})); ctrl.close(); return;
          }
          if (verify.status !== 200 || vdata.status !== "paid") {
            ctrl.enqueue(sseEnc("text", { content: `⚠️ 支付验证失败（${vdata.status ?? "unknown"}）。` }));
            ctrl.enqueue(sseEnc("done", {})); ctrl.close(); return;
          }
          if (vdata.intent_credential) intentCredential = vdata.intent_credential as string;
        }

        // Build AP2 checkout_mandate with optional biometric
        let ap2Mandate: string | null = null;
        const ts = Math.floor(Date.now() / 1000);
        if (intentCredential) {
          const verification: Record<string, unknown> = { method: "server_query", verified_at: ts };
          if (biometric && biometric.confidence) {
            verification.biometric = {
              method:         biometric.method ?? "face_recognition",
              provider:       biometric.provider ?? "face-api.js",
              confidence:     biometric.confidence,
              voice_verified: biometric.voice_verified ?? false,
              verified_at:    biometric.verified_at ?? ts,
            };
          }
          ap2Mandate = buildMandate({
            type: "alipay_intent_credential", act_version: "ACT/1.0",
            credential: intentCredential, checkout_id, alipay_order_id: alipayOrderId,
            amount: { value: amount, currency }, agent_id: "ucp-agent-v1",
            issued_by: `${base}/profile`, iat: ts, exp: ts + 300,
            verification,
          });
          ctrl.enqueue(sseEnc("ap2_mandate", {
            type: "alipay_intent_credential", act: "ACT/1.0", verified: true,
            biometric_included: !!(biometric?.confidence),
            mandate_preview: ap2Mandate.slice(0, 72) + "…",
          }));
        } else if (gpayToken) {
          ap2Mandate = buildMandate({ type: "google_pay_token",
            token_preview: gpayToken.slice(0, 20) + "…", checkout_id,
            agent_id: "ucp-agent-v1", issued_by: `${base}/profile`, iat: ts });
        }

        ctrl.enqueue(sseEnc("text", { content: "✅ 支付已授权，正在完成结账…" }));

        // Complete checkout
        const cid = `cc-${crypto.randomUUID().slice(0, 8)}`;
        ctrl.enqueue(sseEnc("tool_call", { id: cid, name: "ucp_complete_checkout",
          args: { checkout_id, ap2_mandate: ap2Mandate ? "included" : null } }));
        const completeRes = await mockPost(mock, `/checkout-sessions/${encodeURIComponent(String(checkout_id))}/complete`,
          base, ap2Mandate ? { ap2: { checkout_mandate: ap2Mandate } } : {}, String(token));
        ctrl.enqueue(sseEnc("tool_result", { id: cid, name: "ucp_complete_checkout",
          status: completeRes.status, data: completeRes.data }));

        const cdata = completeRes.data as Record<string, unknown>;
        if (completeRes.status >= 400) {
          const msgs = (cdata.messages as Array<Record<string, string>>) ?? [];
          ctrl.enqueue(sseEnc("text", { content: `❌ 结账失败：${msgs[0]?.content ?? msgs[0]?.message ?? "未知错误"}` }));
          ctrl.enqueue(sseEnc("done", {})); ctrl.close(); return;
        }

        const pickupCode = cdata.pickup_code as string | undefined;
        const pickupUrl  = cdata.pickup_url  as string | undefined;
        const orderId    = cdata.order_id as string;

        // Post-checkout: deduct welfare if balance available
        const amountNum = Number(amount);
        const w = await getWelfare(kv, userId);
        let deducted = 0;
        if (w.remaining > 0 && amountNum > 0) {
          deducted = Math.min(amountNum / 100, w.remaining);
          w.used      = Math.round((w.used + deducted) * 100) / 100;
          w.remaining = Math.round((w.remaining - deducted) * 100) / 100;
          await putWelfare(kv, userId, w);
          const wid = `wf-${crypto.randomUUID().slice(0, 8)}`;
          ctrl.enqueue(sseEnc("tool_call", { id: wid, name: "deduct_welfare",
            args: { user_id: userId, amount_yuan: deducted } }));
          ctrl.enqueue(sseEnc("tool_result", { id: wid, name: "deduct_welfare", status: 200,
            data: { ok: true, deducted_yuan: deducted, remaining_yuan: w.remaining }}));
        }

        // Notify ops via Slack
        const notifyMsg = `✅ 出货成功\n商品：${product}\n金额：¥${(amountNum/100).toFixed(2)}\n` +
          (deducted > 0 ? `福利抵扣：¥${deducted.toFixed(2)}\n` : "") +
          `取货码：${pickupCode ?? "N/A"}\n用户：${userId}\n订单：${orderId}`;
        await slackPost(env.SLACK_BOT_TOKEN, env.SLACK_OPS_CHANNEL || "#vending-ops", notifyMsg);

        // Track order (brief poll)
        const tid = `to-${crypto.randomUUID().slice(0, 8)}`;
        ctrl.enqueue(sseEnc("tool_call", { id: tid, name: "ucp_track_order", args: { order_id: orderId } }));
        await new Promise<void>(r => setTimeout(r, 800));
        const trackRes = await mockGet(mock, `/orders/${orderId}`, base, String(token));
        ctrl.enqueue(sseEnc("tool_result", { id: tid, name: "ucp_track_order",
          status: trackRes.status, data: trackRes.data }));

        const tdata = trackRes.data as Record<string, unknown>;
        const events = ((tdata.fulfillment as Record<string, unknown>)?.events as Array<Record<string, string>> ?? []);
        const amountStr = `¥${(amountNum / 100).toFixed(2)}`;
        const welfareNote = deducted > 0 ? `\n企业福利抵扣 ¥${deducted.toFixed(2)}，剩余 ¥${w.remaining.toFixed(2)}` : "";

        ctrl.enqueue(sseEnc("payment_done", {
          order_id: orderId, amount: amountStr, product,
          events: events.map(e => e.type ?? e.name ?? ""),
          pickup_code: pickupCode, pickup_url: pickupUrl,
        }));
        ctrl.enqueue(sseEnc("text", { content:
          `🎉 购买成功！\n\n商品：${product}\n支付：${amountStr} ${currency}${welfareNote}\n` +
          (pickupCode ? `\n🏪 **取货码：\`${pickupCode}\`**\n请前往自动贩卖机扫码取货。` : "") }));

      } catch (e) {
        console.error("phase2", e);
        ctrl.enqueue(sseEnc("text", { content: "❌ Agent 异常，请重试。" }));
      }
      ctrl.enqueue(sseEnc("done", {}));
      ctrl.close();
    },
  });
}

// ── Main handler ──────────────────────────────────────────────────────────────
export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (req.method === "OPTIONS") {
      return new Response(null, { headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
      }});
    }

    const url  = new URL(req.url);
    const path = url.pathname;
    const base = agentBase(url);

    if (path === "/profile") {
      return jsonResp({ id: "ucp-agent-v1", name: "UCP Vending Agent", version: "2026-04-08",
        profile_url: `${base}/profile`,
        capabilities: ["dev.ucp.shopping.cart", "dev.ucp.shopping.checkout", "dev.ucp.shopping.order"],
        payment_support: ["alipay_aipay", "google_pay"] });
    }
    if (path === "/health") {
      return jsonResp({ ok: true, bindings: { mock: !!env.MOCK, supply_chain: !!env.SUPPLY_CHAIN, welfare_kv: !!env.WELFARE_KV }});
    }

    if (path === "/api/chat" && req.method === "POST") {
      const body = await req.json().catch(() => ({})) as Record<string, string>;
      const msg  = (body.message ?? "").trim();
      const uid  = body.user_id || "guest";
      if (!msg) return jsonResp({ error: "empty" }, 400);
      return new Response(
        phase1Stream(msg, uid, env.MOCK, env.SUPPLY_CHAIN, env.WELFARE_KV, base, env),
        { headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache",
          "X-Accel-Buffering": "no", "Access-Control-Allow-Origin": "*" }});
    }

    if (path === "/api/alipay-create-order" && req.method === "POST") {
      const body = await req.json().catch(() => ({})) as Record<string, unknown>;
      const res = await mockPost(env.MOCK, "/alipay/create-order", base, {
        checkout_id: body.checkout_id, merchant_order_id: body.checkout_id,
        amount: body.amount, currency: body.currency ?? "CNY",
        product_name: body.product_name ?? "",
        agent_pay_info: { agent_type: "AI_AGENT", agent_id: "ucp-agent-v1", agent_name: "UCP Vending Agent" },
      }, String(body.token ?? ""));
      return jsonResp(res);
    }

    if (path === "/api/confirm-payment" && req.method === "POST") {
      const body = await req.json().catch(() => ({})) as Record<string, unknown>;
      return new Response(phase2Stream(
        String(body.payment_id ?? ""), String(body.gpay_token ?? "") || null,
        String(body.alipay_order_id ?? "") || null, String(body.intent_credential ?? "") || null,
        String(body.user_id ?? "guest"),
        (body.biometric as Record<string, unknown>) ?? null,
        env.MOCK, env.SUPPLY_CHAIN, env.WELFARE_KV, base, env,
      ), { headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no", "Access-Control-Allow-Origin": "*" }});
    }

    // Serve static assets (index.html etc.)
    return env.ASSETS.fetch(req);
  },
};
