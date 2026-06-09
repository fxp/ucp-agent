export interface Env {
  SC_KV: KVNamespace;
  DASHBOARD: DurableObjectNamespace;
}

interface Supplier {
  id: string;
  name: string;
  lead_time_days: number;
  min_order_yuan: number;
}

interface Machine {
  id: string;
  name: string;
  location: string;
}

interface Sku {
  sku_id: string;
  name: string;
  cost_fen: number;
  retail_fen: number;
  moq: number;
  supplier_id: string;
}

interface LaneConfig {
  lane_id: string;
  machine_id: string;
  sku_id: string;
  name: string;
  price_fen: number;
  currency: string;
  min_qty: number;
  capacity: number;
  location: string;
}

const PO_STATUSES = ["draft", "submitted", "acknowledged", "shipped", "received", "stocked"];

function nowIso(): string { return new Date().toISOString(); }
function uid(): string { return crypto.randomUUID().replace(/-/g, "").slice(0, 12); }

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
    },
  });
}

function notFound(msg = "Not found"): Response { return json({ error: msg }, 404); }
function badRequest(msg: string): Response { return json({ error: msg }, 400); }

async function listAll<T>(kv: KVNamespace, prefix: string): Promise<T[]> {
  const list  = await kv.list({ prefix });
  const items = await Promise.all(list.keys.map(k => kv.get(k.name)));
  return items.filter(Boolean).map(v => JSON.parse(v!) as T);
}

async function getSupplier(kv: KVNamespace, id: string): Promise<Supplier | null> {
  const v = await kv.get(`supplier:${id}`); return v ? JSON.parse(v) : null;
}
async function getMachine(kv: KVNamespace, id: string): Promise<Machine | null> {
  const v = await kv.get(`machine:${id}`); return v ? JSON.parse(v) : null;
}
async function getSku(kv: KVNamespace, skuId: string): Promise<Sku | null> {
  const v = await kv.get(`sku:${skuId}`); return v ? JSON.parse(v) : null;
}
async function getLane(kv: KVNamespace, machineId: string, laneId: string): Promise<LaneConfig | null> {
  const v = await kv.get(`lane:${machineId}:${laneId}`); return v ? JSON.parse(v) : null;
}
async function getAllLanes(kv: KVNamespace, machineId: string): Promise<LaneConfig[]> {
  return listAll<LaneConfig>(kv, `lane:${machineId}:`);
}

type InvRow = LaneConfig & { qty: number; last_restocked_at: string | null; low_stock: boolean };

async function getInventoryAll(kv: KVNamespace, machineId: string): Promise<InvRow[]> {
  const lanes = await getAllLanes(kv, machineId);
  return Promise.all(lanes.map(async (lane) => {
    const val  = await kv.get(`inv:${machineId}:${lane.lane_id}`);
    const live = val ? JSON.parse(val) : { qty: 0, last_restocked_at: null };
    return { ...lane, qty: live.qty ?? 0, last_restocked_at: live.last_restocked_at ?? null, low_stock: (live.qty ?? 0) <= lane.min_qty };
  }));
}

async function getMachineStats(kv: KVNamespace, machineId: string) {
  const inv = await getInventoryAll(kv, machineId);
  return {
    total_items:     inv.reduce((s, l) => s + l.qty, 0),
    low_stock_count: inv.filter(l => l.low_stock).length,
  };
}

async function stockPO(kv: KVNamespace, machineId: string, po: Record<string, unknown>): Promise<void> {
  const now   = nowIso();
  const items = po.items as Array<{ sku_id: string; qty: number }>;
  for (const item of items) {
    const lanes = (await getAllLanes(kv, machineId)).filter(l => l.sku_id === item.sku_id);
    for (const lane of lanes) {
      const val    = await kv.get(`inv:${machineId}:${lane.lane_id}`);
      const live   = val ? JSON.parse(val) : { qty: 0 };
      const newQty = Math.min(live.qty + item.qty, lane.capacity);
      await kv.put(`inv:${machineId}:${lane.lane_id}`, JSON.stringify({ qty: newQty, last_restocked_at: now }));
    }
    const pres = (await listAll<Record<string, unknown>>(kv, "pre:")).filter(p => p["sku_id"] === item.sku_id && p["status"] === "pending");
    for (const pre of pres) {
      pre["status"]     = "stock_arrived";
      pre["updated_at"] = now;
      await kv.put(`pre:${pre["id"]}`, JSON.stringify(pre));
    }
  }
}

async function handleRequest(request: Request, env: Env): Promise<Response> {
  if (request.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
      },
    });
  }

  const url  = new URL(request.url);
  const path = url.pathname.replace(/\/$/, "") || "/";
  const meth = request.method;
  const kv   = env.SC_KV;

  if (meth === "GET" && path === "/health") return json({ status: "ok", time: nowIso() });

  // ── WebSocket dashboard stream ────────────────────────────────────────────
  if (meth === "GET" && path === "/ws") {
    const id   = env.DASHBOARD.idFromName("main");
    const stub = env.DASHBOARD.get(id);
    return stub.fetch(request);
  }

  // Serve dashboard
  if (meth === "GET" && (path === "/" || path === "/dashboard")) {
    return new Response(null, { status: 302, headers: { Location: "/index.html" } });
  }

  if (path === "/skus") {
    if (meth === "GET") return json(await listAll<Sku>(kv, "sku:"));
    if (meth === "POST") {
      const b = await request.json() as Record<string, unknown>;
      if (!b["sku_id"])                         return badRequest("sku_id required");
      if (!b["name"])                           return badRequest("name required");
      if (typeof b["cost_fen"]   !== "number")  return badRequest("cost_fen required (number)");
      if (typeof b["retail_fen"] !== "number")  return badRequest("retail_fen required (number)");
      if (typeof b["moq"]        !== "number")  return badRequest("moq required (number)");
      if (!b["supplier_id"])                    return badRequest("supplier_id required");
      const sku: Sku = { sku_id: b["sku_id"] as string, name: b["name"] as string, cost_fen: b["cost_fen"] as number, retail_fen: b["retail_fen"] as number, moq: b["moq"] as number, supplier_id: b["supplier_id"] as string };
      await kv.put(`sku:${sku.sku_id}`, JSON.stringify(sku));
      return json(sku, 201);
    }
  }

  const skuMatch = path.match(/^\/skus\/([^/]+)$/);
  if (skuMatch) {
    const skuId = skuMatch[1];
    if (meth === "GET") { const sku = await getSku(kv, skuId); return sku ? json(sku) : notFound("SKU not found"); }
    if (meth === "PUT") {
      const existing = await getSku(kv, skuId);
      if (!existing) return notFound("SKU not found");
      const updated: Sku = { ...existing, ...await request.json() as Partial<Sku>, sku_id: skuId };
      await kv.put(`sku:${skuId}`, JSON.stringify(updated));
      return json(updated);
    }
    if (meth === "DELETE") { await kv.delete(`sku:${skuId}`); return json({ deleted: true, sku_id: skuId }); }
  }

  if (path === "/machines") {
    if (meth === "GET") {
      const machines = await listAll<Machine>(kv, "machine:");
      return json(await Promise.all(machines.map(async m => ({ ...m, ...await getMachineStats(kv, m.id) }))));
    }
    if (meth === "POST") {
      const b = await request.json() as Record<string, unknown>;
      if (!b["id"])   return badRequest("id required");
      if (!b["name"]) return badRequest("name required");
      const machine: Machine = { id: b["id"] as string, name: b["name"] as string, location: (b["location"] as string) || "" };
      await kv.put(`machine:${machine.id}`, JSON.stringify(machine));
      return json(machine, 201);
    }
  }

  const machineMatch = path.match(/^\/machines\/([^/]+)$/);
  if (machineMatch) {
    const mid = machineMatch[1];
    if (meth === "GET") {
      const machine = await getMachine(kv, mid);
      if (!machine) return notFound("Machine not found");
      return json({ ...machine, ...await getMachineStats(kv, mid), lanes: await getAllLanes(kv, mid) });
    }
    if (meth === "PUT") {
      const existing = await getMachine(kv, mid);
      if (!existing) return notFound("Machine not found");
      const updated: Machine = { ...existing, ...await request.json() as Partial<Machine>, id: mid };
      await kv.put(`machine:${mid}`, JSON.stringify(updated));
      return json(updated);
    }
    if (meth === "DELETE") { await kv.delete(`machine:${mid}`); return json({ deleted: true, id: mid }); }
  }

  const machineLanesMatch = path.match(/^\/machines\/([^/]+)\/lanes$/);
  if (machineLanesMatch) {
    const mid = machineLanesMatch[1];
    if (!await getMachine(kv, mid)) return notFound("Machine not found");
    if (meth === "GET") return json(await getAllLanes(kv, mid));
    if (meth === "POST") {
      const b = await request.json() as Record<string, unknown>;
      if (!b["lane_id"])                      return badRequest("lane_id required");
      if (!b["sku_id"])                       return badRequest("sku_id required");
      if (!b["name"])                         return badRequest("name required");
      if (typeof b["price_fen"] !== "number") return badRequest("price_fen required (number)");
      if (typeof b["min_qty"]   !== "number") return badRequest("min_qty required (number)");
      if (typeof b["capacity"]  !== "number") return badRequest("capacity required (number)");
      const sku = await getSku(kv, b["sku_id"] as string);
      if (!sku) return badRequest(`sku_not_found: ${b["sku_id"]}`);
      const lane: LaneConfig = { lane_id: b["lane_id"] as string, machine_id: mid, sku_id: b["sku_id"] as string, name: b["name"] as string, price_fen: b["price_fen"] as number, currency: (b["currency"] as string) || "CNY", min_qty: b["min_qty"] as number, capacity: b["capacity"] as number, location: (b["location"] as string) || "" };
      await kv.put(`lane:${mid}:${lane.lane_id}`, JSON.stringify(lane));
      if (!await kv.get(`inv:${mid}:${lane.lane_id}`)) {
        await kv.put(`inv:${mid}:${lane.lane_id}`, JSON.stringify({ qty: 0, last_restocked_at: null }));
      }
      return json(lane, 201);
    }
  }

  const machineLaneMatch = path.match(/^\/machines\/([^/]+)\/lanes\/([^/]+)$/);
  if (machineLaneMatch) {
    const [, mid, laneId] = machineLaneMatch;
    if (!await getMachine(kv, mid)) return notFound("Machine not found");
    if (meth === "PUT") {
      const existing = await getLane(kv, mid, laneId);
      if (!existing) return notFound("Lane not found");
      const b = await request.json() as Partial<LaneConfig>;
      if (b.sku_id && b.sku_id !== existing.sku_id && !await getSku(kv, b.sku_id)) return badRequest(`sku_not_found: ${b.sku_id}`);
      const updated: LaneConfig = { ...existing, ...b, lane_id: laneId, machine_id: mid };
      await kv.put(`lane:${mid}:${laneId}`, JSON.stringify(updated));
      return json(updated);
    }
    if (meth === "DELETE") {
      await kv.delete(`lane:${mid}:${laneId}`);
      await kv.delete(`inv:${mid}:${laneId}`);
      return json({ deleted: true, machine_id: mid, lane_id: laneId });
    }
  }

  const machineInvMatch = path.match(/^\/machines\/([^/]+)\/inventory$/);
  if (machineInvMatch && meth === "GET") {
    const mid = machineInvMatch[1];
    if (!await getMachine(kv, mid)) return notFound("Machine not found");
    return json(await getInventoryAll(kv, mid));
  }

  if (path === "/inventory") {
    if (meth === "GET") {
      const mid     = url.searchParams.get("machine_id") || "vm-001";
      const lowOnly = url.searchParams.get("low_stock_only") === "true";
      let inv = await getInventoryAll(kv, mid);
      if (lowOnly) inv = inv.filter(i => i.low_stock);
      return json(inv);
    }
  }

  const invLaneMatch = path.match(/^\/inventory\/([^/]+)$/);
  if (invLaneMatch && meth === "GET") {
    const laneId    = invLaneMatch[1];
    const machineId = url.searchParams.get("machine_id") || "vm-001";
    const lane      = await getLane(kv, machineId, laneId);
    if (!lane) return notFound("Lane not found");
    const val  = await kv.get(`inv:${machineId}:${laneId}`);
    const live = val ? JSON.parse(val) : { qty: 0, last_restocked_at: null };
    return json({ ...lane, qty: live.qty ?? 0, last_restocked_at: live.last_restocked_at, low_stock: (live.qty ?? 0) <= lane.min_qty });
  }

  if (path === "/inventory/search" && meth === "GET") {
    const q = (url.searchParams.get("name") || url.searchParams.get("q") || "").toLowerCase().trim();
    if (!q) return badRequest("name or q query param required");
    const machines = await listAll<Machine>(kv, "machine:");
    const results: Record<string, unknown>[] = [];
    for (const m of machines) {
      for (const lane of await getInventoryAll(kv, m.id)) {
        if (lane.name.toLowerCase().includes(q) || lane.sku_id.toLowerCase().includes(q)) {
          results.push({ machine_id: m.id, machine_name: m.name, machine_location: m.location, lane_id: lane.lane_id, sku_id: lane.sku_id, name: lane.name, price_fen: lane.price_fen, currency: lane.currency, qty: lane.qty, available: lane.qty > 0, low_stock: lane.low_stock });
        }
      }
    }
    return json(results);
  }

  if (path === "/inventory/restock" && meth === "POST") {
    const b         = await request.json() as Record<string, unknown>;
    const items     = b["items"] as Array<{ lane_id: string; sku_id: string; qty: number }>;
    if (!items || !Array.isArray(items)) return badRequest("items required");
    const machineId = (b["machine_id"] as string) || "vm-001";
    const now       = nowIso();
    const results: Record<string, unknown>[] = [];
    for (const item of items) {
      const laneConfig = await getLane(kv, machineId, item.lane_id);
      if (!laneConfig) { results.push({ lane_id: item.lane_id, error: "Lane not found" }); continue; }
      if (laneConfig.sku_id !== item.sku_id) { results.push({ lane_id: item.lane_id, error: "SKU mismatch" }); continue; }
      const val    = await kv.get(`inv:${machineId}:${item.lane_id}`);
      const live   = val ? JSON.parse(val) : { qty: 0 };
      const newQty = Math.min(live.qty + item.qty, laneConfig.capacity);
      await kv.put(`inv:${machineId}:${item.lane_id}`, JSON.stringify({ qty: newQty, last_restocked_at: now }));
      results.push({ lane_id: item.lane_id, sku_id: item.sku_id, added: item.qty, new_qty: newQty });
    }
    return json({ po_id: b["po_id"] ?? null, restocked_at: now, results });
  }

  if (path === "/suppliers") {
    if (meth === "GET") return json(await listAll<Supplier>(kv, "supplier:"));
    if (meth === "POST") {
      const b = await request.json() as Record<string, unknown>;
      if (!b["id"])                              return badRequest("id required");
      if (!b["name"])                            return badRequest("name required");
      if (typeof b["lead_time_days"] !== "number") return badRequest("lead_time_days required (number)");
      if (typeof b["min_order_yuan"] !== "number") return badRequest("min_order_yuan required (number)");
      const supplier: Supplier = { id: b["id"] as string, name: b["name"] as string, lead_time_days: b["lead_time_days"] as number, min_order_yuan: b["min_order_yuan"] as number };
      await kv.put(`supplier:${supplier.id}`, JSON.stringify(supplier));
      return json(supplier, 201);
    }
  }

  const supplierMatch = path.match(/^\/suppliers\/([^/]+)$/);
  if (supplierMatch) {
    const sid = supplierMatch[1];
    if (meth === "GET") { const s = await getSupplier(kv, sid); return s ? json(s) : notFound("Supplier not found"); }
    if (meth === "PUT") {
      const existing = await getSupplier(kv, sid);
      if (!existing) return notFound("Supplier not found");
      const updated: Supplier = { ...existing, ...await request.json() as Partial<Supplier>, id: sid };
      await kv.put(`supplier:${sid}`, JSON.stringify(updated));
      return json(updated);
    }
    if (meth === "DELETE") { await kv.delete(`supplier:${sid}`); return json({ deleted: true, id: sid }); }
  }

  const supplierSkuMatch = path.match(/^\/suppliers\/([^/]+)\/skus$/);
  if (supplierSkuMatch && meth === "GET") {
    const sid      = supplierSkuMatch[1];
    const supplier = await getSupplier(kv, sid);
    if (!supplier) return notFound("Supplier not found");
    const kw   = (url.searchParams.get("keyword") || "").toLowerCase();
    let skus   = (await listAll<Sku>(kv, "sku:")).filter(s => s.supplier_id === sid);
    if (kw) skus = skus.filter(s => s.name.toLowerCase().includes(kw) || s.sku_id.toLowerCase().includes(kw));
    return json(skus);
  }

  if (path === "/purchase-orders") {
    if (meth === "GET") {
      let pos = await listAll<Record<string, unknown>>(kv, "po:");
      const sf = url.searchParams.get("status");
      const pf = url.searchParams.get("supplier_id");
      if (sf) pos = pos.filter(p => p["status"] === sf);
      if (pf) pos = pos.filter(p => p["supplier_id"] === pf);
      return json(pos);
    }
    if (meth === "POST") {
      const b        = await request.json() as Record<string, unknown>;
      const supplier = await getSupplier(kv, b["supplier_id"] as string);
      if (!b["supplier_id"]) return badRequest("supplier_id required");
      if (!supplier)         return badRequest("supplier_not_found: " + b["supplier_id"]);
      const items    = b["items"] as Array<{ sku_id: string; qty: number }>;
      if (!Array.isArray(items) || !items.length) return badRequest("items required");
      const resolvedItems: Record<string, unknown>[] = [];
      for (const item of items) {
        if (!item.sku_id)          return badRequest("sku_id required in items");
        const sku = await getSku(kv, item.sku_id);
        if (!sku)                  return badRequest(`sku_not_found: ${item.sku_id}`);
        if (!item.qty || item.qty < 1) return badRequest("qty must be >= 1");
        resolvedItems.push({ sku_id: item.sku_id, sku_name: sku.name, qty: item.qty, cost_fen: sku.cost_fen });
      }
      const now = nowIso();
      const po  = { id: `PO-${uid()}`, supplier_id: b["supplier_id"], supplier_name: supplier.name, machine_id: b["machine_id"] || "vm-001", items: resolvedItems, note: b["note"] || "", priority: b["priority"] || "normal", status: "draft", created_at: now, updated_at: now, status_history: [{ status: "draft", at: now }] };
      await kv.put(`po:${po.id}`, JSON.stringify(po));
      return json(po, 201);
    }
  }

  const poMatch = path.match(/^\/purchase-orders\/([^/]+)$/);
  if (poMatch && meth === "GET") {
    const val = await kv.get(`po:${poMatch[1]}`);
    return val ? json(JSON.parse(val)) : notFound("PO not found");
  }

  const poAdvanceMatch = path.match(/^\/purchase-orders\/([^/]+)\/advance$/);
  if (poAdvanceMatch && meth === "POST") {
    const val = await kv.get(`po:${poAdvanceMatch[1]}`);
    if (!val) return notFound("PO not found");
    const po    = JSON.parse(val) as Record<string, unknown>;
    const b     = await request.json().catch(() => ({})) as Record<string, unknown>;
    const curIdx = PO_STATUSES.indexOf(po["status"] as string);
    let target: string;
    if (b["to_status"]) {
      const ti = PO_STATUSES.indexOf(b["to_status"] as string);
      if (ti < 0)       return badRequest("Invalid to_status");
      if (ti <= curIdx) return badRequest("Cannot advance to earlier or same status");
      target = b["to_status"] as string;
    } else {
      if (curIdx >= PO_STATUSES.length - 1) return badRequest("PO already at final status");
      target = PO_STATUSES[curIdx + 1];
    }
    const now = nowIso();
    po["status"]      = target;
    po["updated_at"]  = now;
    (po["status_history"] as unknown[]).push({ status: target, at: now });
    if (target === "stocked") await stockPO(kv, (po["machine_id"] as string) ?? "vm-001", po);
    await kv.put(`po:${po["id"]}`, JSON.stringify(po));
    return json(po);
  }

  if (path === "/preorders") {
    if (meth === "GET") {
      let pres = await listAll<Record<string, unknown>>(kv, "pre:");
      const sf = url.searchParams.get("status");
      const uf = url.searchParams.get("user_id");
      if (sf) pres = pres.filter(p => p["status"] === sf);
      if (uf) pres = pres.filter(p => p["user_id"] === uf);
      return json(pres);
    }
    if (meth === "POST") {
      const b   = await request.json() as Record<string, unknown>;
      if (!b["sku_id"])          return badRequest("sku_id required");
      if (!b["qty"] || (b["qty"] as number) < 1) return badRequest("qty must be >= 1");
      if (!b["user_id"])         return badRequest("user_id required");
      const sku = await getSku(kv, b["sku_id"] as string);
      const now = nowIso();
      const pre = { id: `PRE-${uid()}`, sku_id: b["sku_id"], sku_name: b["sku_name"] || sku?.name || b["sku_id"], qty: b["qty"], user_id: b["user_id"], note: b["note"] || "", notify_channel: b["notify_channel"] || "none", status: "pending", created_at: now, updated_at: now, notified_at: null };
      await kv.put(`pre:${pre.id}`, JSON.stringify(pre));
      return json(pre, 201);
    }
  }

  const preMatch = path.match(/^\/preorders\/([^/]+)$/);
  if (preMatch && meth === "GET") {
    const val = await kv.get(`pre:${preMatch[1]}`);
    return val ? json(JSON.parse(val)) : notFound("Preorder not found");
  }

  const preNotifyMatch = path.match(/^\/preorders\/([^/]+)\/notify$/);
  if (preNotifyMatch && meth === "POST") {
    const val = await kv.get(`pre:${preNotifyMatch[1]}`);
    if (!val) return notFound("Preorder not found");
    const pre     = JSON.parse(val) as Record<string, unknown>;
    const now     = nowIso();
    pre["notified_at"] = now;
    pre["updated_at"]  = now;
    if (pre["status"] === "stock_arrived") pre["status"] = "notified";
    await kv.put(`pre:${pre["id"]}`, JSON.stringify(pre));
    return json(pre);
  }

  if (path === "/internal/daily-order/preview" && meth === "GET") {
    const machineId = url.searchParams.get("machine_id") || "vm-001";
    return json(await buildOrderPreview(kv, machineId));
  }

  if (path === "/internal/daily-order" && meth === "POST") {
    const b         = await request.json().catch(() => ({})) as Record<string, unknown>;
    const dry_run   = b["dry_run"] === true;
    const machineId = (b["machine_id"] as string) || "vm-001";
    return json(await triggerDailyOrder(kv, machineId, dry_run), dry_run ? 200 : 201);
  }

  return notFound("Endpoint not found");
}

async function buildBySupplier(kv: KVNamespace, machineId: string) {
  const inv        = await getInventoryAll(kv, machineId);
  const bySupplier: Record<string, Array<{ sku_id: string; qty: number; sku: Sku }>> = {};
  for (const lane of inv) {
    if (lane.qty >= lane.min_qty) continue;
    const sku = await getSku(kv, lane.sku_id);
    if (!sku) continue;
    const reorderQty = Math.max(sku.moq, lane.capacity - lane.qty);
    const sid        = sku.supplier_id;
    if (!bySupplier[sid]) bySupplier[sid] = [];
    const existing = bySupplier[sid].find(i => i.sku_id === lane.sku_id);
    if (existing) existing.qty += reorderQty;
    else bySupplier[sid].push({ sku_id: lane.sku_id, qty: reorderQty, sku });
  }
  return bySupplier;
}

async function buildOrderPreview(kv: KVNamespace, machineId: string) {
  const bySupplier = await buildBySupplier(kv, machineId);
  const suggestions: Record<string, unknown>[] = [];
  for (const [sid, items] of Object.entries(bySupplier)) {
    const supplier = await getSupplier(kv, sid);
    if (!supplier) continue;
    const total_cost = items.reduce((s, i) => s + i.sku.cost_fen * i.qty, 0);
    suggestions.push({ supplier_id: sid, supplier_name: supplier.name, min_order_yuan: supplier.min_order_yuan, total_cost_fen: total_cost, meets_minimum: total_cost >= supplier.min_order_yuan * 100, items: items.map(i => ({ sku_id: i.sku_id, sku_name: i.sku.name, qty: i.qty, cost_fen: i.sku.cost_fen * i.qty })) });
  }
  return { preview_at: nowIso(), machine_id: machineId, suggestions };
}

async function triggerDailyOrder(kv: KVNamespace, machineId: string, dry_run: boolean) {
  const bySupplier = await buildBySupplier(kv, machineId);
  const created: Record<string, unknown>[] = [];
  const skipped: Record<string, unknown>[] = [];
  for (const [sid, items] of Object.entries(bySupplier)) {
    const supplier  = await getSupplier(kv, sid);
    if (!supplier) continue;
    const totalCost = items.reduce((s, i) => s + i.sku.cost_fen * i.qty, 0);
    if (totalCost < supplier.min_order_yuan * 100) {
      skipped.push({ supplier_id: sid, reason: "below_min_order", total_cost_fen: totalCost });
      continue;
    }
    if (!dry_run) {
      const now = nowIso();
      const po  = { id: `PO-${uid()}`, supplier_id: sid, supplier_name: supplier.name, machine_id: machineId, items: items.map(i => ({ sku_id: i.sku_id, sku_name: i.sku.name, qty: i.qty, cost_fen: i.sku.cost_fen })), note: "auto daily order", priority: "normal", status: "draft", created_at: now, updated_at: now, status_history: [{ status: "draft", at: now }] };
      await kv.put(`po:${po.id}`, JSON.stringify(po));
      created.push(po);
    } else {
      created.push({ supplier_id: sid, items: items.map(i => ({ sku_id: i.sku_id, qty: i.qty })), total_cost_fen: totalCost, dry_run: true });
    }
  }
  return { dry_run, machine_id: machineId, created_at: nowIso(), created, skipped };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    return handleRequest(request, env);
  },
  async scheduled(_event: ScheduledEvent, env: Env, _ctx: ExecutionContext): Promise<void> {
    console.log("[cron] Daily auto-order at", nowIso());
    await triggerDailyOrder(env.SC_KV, "vm-001", false);
  },
};

export class DashboardRoom implements DurableObject {
  constructor(private state: DurableObjectState, private env: Env) {}

  async fetch(request: Request): Promise<Response> {
    if (request.headers.get("Upgrade") !== "websocket") {
      return new Response("Expected WebSocket upgrade", { status: 426 });
    }
    const pair = new WebSocketPair();
    const [client, server] = [pair[0], pair[1]];
    this.state.acceptWebSocket(server);

    const snapshot = await this.buildData();
    server.send(JSON.stringify({ type: "snapshot", ...snapshot }));

    const alarm = await this.state.storage.getAlarm();
    if (!alarm) await this.state.storage.setAlarm(Date.now() + 5000);

    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(_ws: WebSocket, _msg: string | ArrayBuffer): Promise<void> {}

  async webSocketClose(ws: WebSocket): Promise<void> {
    ws.close();
    if (this.state.getWebSockets().length === 0) {
      await this.state.storage.deleteAlarm();
    }
  }

  webSocketError(ws: WebSocket, _err: unknown): void {
    ws.close(1011, "error");
  }

  async alarm(): Promise<void> {
    const sockets = this.state.getWebSockets();
    if (!sockets.length) return;
    const data = await this.buildData();
    const msg  = JSON.stringify({ type: "update", ...data });
    for (const ws of sockets) { try { ws.send(msg); } catch {} }
    await this.state.storage.setAlarm(Date.now() + 5000);
  }

  private async buildData() {
    const kv       = this.env.SC_KV;
    const machines = await listAll<Machine>(kv, "machine:");
    const invByMachine: Record<string, InvRow[]> = {};
    for (const m of machines) invByMachine[m.id] = await getInventoryAll(kv, m.id);
    return {
      inventory:      Object.values(invByMachine).flat(),
      inv_by_machine: invByMachine,
      pos:            await listAll(kv, "po:"),
      preorders:      await listAll(kv, "pre:"),
      suppliers:      await listAll<Supplier>(kv, "supplier:"),
      preview:        await buildOrderPreview(kv, machines[0]?.id ?? "vm-001"),
    };
  }
}
