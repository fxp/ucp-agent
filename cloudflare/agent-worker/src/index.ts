/**
 * UCP Agent — Cloudflare Worker
 * GLM-4 powered buyer-side UCP agent + chat frontend.
 * In-memory pending state (dev use; resets on cold start).
 */

export interface Env {
  GLM_API_KEY: string;
  MERCHANT_URL: string;
  ASSETS: Fetcher;
  MOCK: Fetcher;
}

const GLM_URL   = "https://open.bigmodel.cn/api/paas/v4/chat/completions";
const GLM_MODEL = "glm-4-flash";

// ── In-memory pending payment state ──────────────────────────────────────────
const _pending = new Map<string, {
  token: string; checkout_id: string; amount: number;
  currency: string; product_name: string; lane_id: string;
}>();

// ── Helpers ───────────────────────────────────────────────────────────────────
const enc = new TextEncoder();

function sseEnc(event: string, data: object): Uint8Array {
  return enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
  });
}

function agentBase(url: URL): string {
  return `${url.protocol}//${url.host}`;
}

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
    body: body !== undefined ? JSON.stringify(body) : undefined,
    headers: ucpHeaders(base, token) }));
  return { status: r.status, data: await r.json().catch(() => ({})) };
}

// ── Tool schemas ───────────────────────────────────────────────────────────────
const TOOLS = [
  { type: "function", function: {
    name: "ucp_discover",
    description: "GET /.well-known/ucp — 发现商户能力和支付处理器。",
    parameters: { type: "object", properties: {}, required: [] },
  }},
  { type: "function", function: {
    name: "ucp_get_token",
    description: "POST /oauth/token — 获取 OAuth 2.0 Bearer Token（client_credentials）。",
    parameters: { type: "object", properties: {}, required: [] },
  }},
  { type: "function", function: {
    name: "ucp_browse_catalog",
    description: "POST /cart-sessions — 浏览商品目录，返回包含 lane_id、价格、库存的列表。",
    parameters: { type: "object",
      properties: { token: { type: "string" } },
      required: ["token"] },
  }},
  { type: "function", function: {
    name: "ucp_create_checkout",
    description: "POST /checkout-sessions — 为选中商品创建结账会话，返回 checkout_id 和价格。",
    parameters: { type: "object",
      properties: {
        token:       { type: "string" },
        lane_id:     { type: "string", description: "来自目录的货道ID" },
        buyer_email: { type: "string" },
      },
      required: ["token", "lane_id", "buyer_email"] },
  }},
  { type: "function", function: {
    name: "ucp_request_payment",
    description: "结账前必须调用。请求用户支付授权。提供结账总额后流程暂停，等待用户确认。",
    parameters: { type: "object",
      properties: {
        token:        { type: "string" },
        checkout_id:  { type: "string" },
        amount:       { type: "integer", description: "金额（分），例如 500 = ¥5.00" },
        currency:     { type: "string" },
        product_name: { type: "string" },
        lane_id:      { type: "string" },
      },
      required: ["token", "checkout_id", "amount", "currency", "product_name", "lane_id"] },
  }},
];

// ── Tool execution ─────────────────────────────────────────────────────────────
async function execTool(name: string, args: Record<string, unknown>, mock: Fetcher, base: string) {
  switch (name) {
    case "ucp_discover":
      return mockGet(mock, "/.well-known/ucp", base);
    case "ucp_get_token":
      return mockPost(mock, "/oauth/token", base, undefined, undefined,
        "grant_type=client_credentials&client_id=demo&client_secret=demo");
    case "ucp_browse_catalog":
      return mockPost(mock, "/cart-sessions", base, {}, args.token as string);
    case "ucp_create_checkout": {
      const profile = await mockGet(mock, "/.well-known/ucp", base);
      const handlers = ((profile.data as Record<string, unknown>)?.payment_handlers ?? []) as unknown[];
      const res = await mockPost(mock, "/checkout-sessions", base, {
        line_items: [{ id: args.lane_id, quantity: 1 }],
        buyer: { email: args.buyer_email },
        payment: { handler_id: "alipay_aipay", instrument: {} },
      }, args.token as string);
      if (res.data && typeof res.data === "object")
        (res.data as Record<string, unknown>)._available_handlers = handlers;
      return res;
    }
    case "ucp_request_payment": {
      const pid = crypto.randomUUID();
      let alipayConfig: unknown = null, gpayConfig: unknown = null;
      try {
        const profile = await mockGet(mock, "/.well-known/ucp", base);
        const handlers = ((profile.data as Record<string, unknown>)?.payment_handlers ?? []) as Array<Record<string, unknown>>;
        for (const h of handlers) {
          if (h.handler_id === "alipay_aipay") alipayConfig = h.config ?? h.ap2;
          if (h.handler_id === "google_pay")   gpayConfig   = h.config;
        }
      } catch { /* ignore */ }
      _pending.set(pid, {
        token: args.token as string, checkout_id: args.checkout_id as string,
        amount: args.amount as number, currency: args.currency as string,
        product_name: args.product_name as string, lane_id: args.lane_id as string,
      });
      return { status: 200, data: {
        payment_id: pid, requires_user_confirmation: true,
        amount: args.amount, currency: args.currency,
        product_name: args.product_name,
        google_pay: gpayConfig, alipay: alipayConfig,
        message: "Payment authorization required. Waiting for user to confirm.",
      }};
    }
    default:
      return { status: 400, data: { error: `unknown tool: ${name}` } };
  }
}

// ── AP2 mandate builder ────────────────────────────────────────────────────────
function buildMandate(body: object): string {
  const h = btoa('{"alg":"ES256","typ":"ap2+sd-jwt","kid":"ucp-agent-v1"}')
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
  const p = btoa(JSON.stringify(body)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
  return `${h}.${p}.mock_platform_sig`;
}

// ── Phase 1 ───────────────────────────────────────────────────────────────────
function phase1Stream(userMessage: string, mock: Fetcher, base: string, env: Env): ReadableStream {
  const systemPrompt =
    `你是 UCP Customer Agent，实现 UCP 2026-04-08 + AP2 Mandate 协议的 AI 购物助手。\n` +
    `Agent Profile: ${base}/profile\n\n` +
    `商户支持：支付宝 AI Pay (ACT/1.0)、Google Pay、预付 Token。\n\n` +
    `用户请求购物时，按以下步骤依次执行：\n` +
    `1. ucp_discover — 发现商户能力\n` +
    `2. ucp_get_token — 获取 OAuth Token\n` +
    `3. ucp_browse_catalog — 浏览商品，找最佳匹配（quantity_available > 0）\n` +
    `4. ucp_create_checkout — 创建结账会话\n` +
    `5. ucp_request_payment — 必须调用！提供 checkout_id、amount（分）、currency、product_name、lane_id。\n` +
    `   调用后简要告知用户商品和价格，不要调用 complete。\n\n` +
    `缺货或离线时说明情况并推荐其他商品。简洁回复，价格用 ¥X.XX 格式。`;

  const messages: unknown[] = [
    { role: "system", content: systemPrompt },
    { role: "user",   content: userMessage },
  ];

  return new ReadableStream({
    async start(ctrl) {
      try {
        for (let i = 0; i < 8; i++) {
          const res = await fetch(GLM_URL, {
            method: "POST",
            headers: { "Authorization": `Bearer ${env.GLM_API_KEY}`, "Content-Type": "application/json" },
            body: JSON.stringify({ model: GLM_MODEL, messages, tools: TOOLS, tool_choice: "auto", stream: false, max_tokens: 800 }),
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
            const toolRes = await execTool(fn, args, mock, base);
            ctrl.enqueue(sseEnc("tool_result", { id: tc.id, name: fn, status: toolRes.status, data: toolRes.data }));
            messages.push({ role: "tool", tool_call_id: tc.id, content: JSON.stringify(toolRes) });

            if (fn === "ucp_request_payment") {
              const d = toolRes.data as Record<string, unknown>;
              if (d?.requires_user_confirmation) {
                ctrl.enqueue(sseEnc("payment_request", {
                  payment_id: d.payment_id, checkout_id: args.checkout_id ?? "",
                  token: args.token ?? "", amount: d.amount, currency: d.currency,
                  product_name: d.product_name, google_pay: d.google_pay, alipay: d.alipay,
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

// ── Phase 2 ───────────────────────────────────────────────────────────────────
function phase2Stream(
  paymentId: string, gpayToken: string | null,
  alipayOrderId: string | null, intentCredential: string | null,
  mock: Fetcher, base: string
): ReadableStream {
  return new ReadableStream({
    async start(ctrl) {
      try {
        const pending = _pending.get(paymentId);
        if (!pending) {
          ctrl.enqueue(sseEnc("text", { content: "⚠️ 支付信息已过期，请重新发起购买。" }));
          ctrl.enqueue(sseEnc("done", {})); ctrl.close(); return;
        }
        _pending.delete(paymentId);
        const { token, checkout_id, amount, currency, product_name: product } = pending;

        // Server-side Alipay verification (安全隔离原则)
        if (alipayOrderId) {
          const vid = `alipay-verify-${crypto.randomUUID()}`;
          ctrl.enqueue(sseEnc("tool_call", { id: vid, name: "alipay_verify_payment",
            args: { alipay_order_id: alipayOrderId, checkout_id } }));
          const verify = await mockGet(mock, `/alipay/query-order/${alipayOrderId}`, base, token);
          ctrl.enqueue(sseEnc("tool_result", { id: vid, name: "alipay_verify_payment",
            status: verify.status, data: verify.data }));

          const vdata = verify.data as Record<string, unknown> ?? {};
          if (vdata.checkout_id && vdata.checkout_id !== checkout_id) {
            ctrl.enqueue(sseEnc("text", { content: "⚠️ 支付订单与结账会话不匹配，拒绝继续。" }));
            ctrl.enqueue(sseEnc("done", {})); ctrl.close(); return;
          }
          if (verify.status !== 200 || vdata.status !== "paid") {
            ctrl.enqueue(sseEnc("text", { content: `⚠️ 服务端支付验证失败（${vdata.status ?? "unknown"}）。` }));
            ctrl.enqueue(sseEnc("done", {})); ctrl.close(); return;
          }
          if (vdata.intent_credential) intentCredential = vdata.intent_credential as string;
        }

        // Build AP2 mandate
        let ap2Mandate: string | null = null;
        const ts = Math.floor(Date.now() / 1000);
        if (intentCredential) {
          const body = {
            type: "alipay_intent_credential", act_version: "ACT/1.0",
            credential: intentCredential, checkout_id, alipay_order_id: alipayOrderId,
            amount: { value: amount, currency }, agent_id: "ucp-shopping-agent-v1",
            issued_by: `${base}/profile`, iat: ts, exp: ts + 300,
            verification: { method: "server_query", verified_at: ts },
          };
          ap2Mandate = buildMandate(body);
          ctrl.enqueue(sseEnc("ap2_mandate", { type: "alipay_intent_credential",
            act: "ACT/1.0", verified: true, mandate_preview: ap2Mandate.slice(0, 72) + "…" }));
        } else if (gpayToken) {
          ap2Mandate = buildMandate({ type: "google_pay_token",
            token_preview: gpayToken.slice(0, 20) + "…",
            checkout_id, agent_id: "ucp-shopping-agent-v1", issued_by: `${base}/profile`, iat: ts });
        }

        ctrl.enqueue(sseEnc("text", { content: "✅ 支付已授权，正在完成结账…" }));

        // Complete checkout
        const cid = `complete-${crypto.randomUUID()}`;
        ctrl.enqueue(sseEnc("tool_call", { id: cid, name: "ucp_complete_checkout",
          args: { checkout_id, ap2_mandate: ap2Mandate ? "included" : null } }));
        const completeBody = ap2Mandate ? { ap2: { checkout_mandate: ap2Mandate } } : {};
        const completeRes = await mockPost(
          mock, `/checkout-sessions/${checkout_id}/complete`,
          base, completeBody, token);
        ctrl.enqueue(sseEnc("tool_result", { id: cid, name: "ucp_complete_checkout",
          status: completeRes.status, data: completeRes.data }));

        const cdata = completeRes.data as Record<string, unknown> ?? {};
        if (completeRes.status >= 400) {
          const msgs = cdata.messages as Array<Record<string, string>> ?? [];
          ctrl.enqueue(sseEnc("text", { content: `❌ 结账失败：${msgs[0]?.content ?? msgs[0]?.message ?? "未知错误"}` }));
          ctrl.enqueue(sseEnc("done", {})); ctrl.close(); return;
        }

        const pickupCode = cdata.pickup_code as string | undefined;
        const pickupUrl  = cdata.pickup_url  as string | undefined;
        const orderId = cdata.order_id as string;
        if (!orderId) {
          ctrl.enqueue(sseEnc("text", { content: "❌ 未获取到订单ID" }));
          ctrl.enqueue(sseEnc("done", {})); ctrl.close(); return;
        }

        // Track order
        const tid = `track-${crypto.randomUUID()}`;
        ctrl.enqueue(sseEnc("tool_call", { id: tid, name: "ucp_track_order", args: { order_id: orderId } }));
        await new Promise<void>(r => setTimeout(r, 1000));
        const trackRes = await mockGet(mock, `/orders/${orderId}`, base, token);
        ctrl.enqueue(sseEnc("tool_result", { id: tid, name: "ucp_track_order",
          status: trackRes.status, data: trackRes.data }));

        const tdata = trackRes.data as Record<string, unknown> ?? {};
        const events = ((tdata.fulfillment as Record<string,unknown>)?.events as Array<Record<string,string>> ?? []);
        const eventNames = events.map(e => e.type ?? e.name ?? "");
        const amountStr = `¥${(amount / 100).toFixed(2)}`;

        ctrl.enqueue(sseEnc("payment_done", {
          order_id: orderId, amount: amountStr, product, events: eventNames,
          pickup_code: pickupCode, pickup_url: pickupUrl,
        }));
        const pickupHint = pickupCode
          ? `\n\n🏪 **取货码：${pickupCode}**\n请前往自动贩卖机扫码取货。`
          : "";
        ctrl.enqueue(sseEnc("text", { content:
          `🎉 购买成功！\n\n商品：${product}\n支付：${amountStr} ${currency}\n订单号：${orderId}${pickupHint}` }));
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
    const mock = env.MOCK;

    // Static files via Workers Assets
    if (path === "/" || path === "/index.html") {
      return env.ASSETS.fetch(req);
    }

    if (path === "/profile") {
      return jsonResp({ id: "ucp-shopping-agent-v1", name: "UCP AI Shopping Agent",
        version: "2026-04-08", profile_url: `${base}/profile`,
        capabilities: ["dev.ucp.shopping.cart", "dev.ucp.shopping.checkout", "dev.ucp.shopping.order"],
        payment_support: ["dev.ucp.vending.prepaid_token"] });
    }

    if (path === "/health") {
      return jsonResp({ ok: true });
    }

    if (path === "/api/chat" && req.method === "POST") {
      const body = await req.json().catch(() => ({})) as Record<string, string>;
      const msg  = (body.message ?? "").trim();
      if (!msg) return jsonResp({ error: "empty" }, 400);
      return new Response(phase1Stream(msg, mock, base, env), { headers: {
        "Content-Type": "text/event-stream", "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no", "Access-Control-Allow-Origin": "*",
      }});
    }

    if (path === "/api/alipay-create-order" && req.method === "POST") {
      const body = await req.json().catch(() => ({})) as Record<string, unknown>;
      const checkoutId = String(body.checkout_id ?? "");
      const token = String(body.token ?? "");
      const res = await mockPost(mock, "/alipay/create-order", base, {
        checkout_id: checkoutId, merchant_order_id: checkoutId,
        amount: body.amount, currency: body.currency ?? "CNY",
        product_name: body.product_name ?? "",
        agent_pay_info: { agent_type: "AI_AGENT", agent_id: "ucp-shopping-agent-v1", agent_name: "UCP AI Shopping Agent" },
      }, token);
      return jsonResp(res);
    }

    if (path === "/api/confirm-payment" && req.method === "POST") {
      const body = await req.json().catch(() => ({})) as Record<string, string>;
      return new Response(phase2Stream(
        body.payment_id ?? "", body.gpay_token ?? null,
        body.alipay_order_id ?? null, body.intent_credential ?? null,
        mock, base
      ), { headers: {
        "Content-Type": "text/event-stream", "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no", "Access-Control-Allow-Origin": "*",
      }});
    }

    return jsonResp({ error: "not_found" }, 404);
  },
};
