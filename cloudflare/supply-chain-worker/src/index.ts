export interface Env {
  SC_KV: KVNamespace;
}

const SUPPLIERS: Record<string, { id: string; name: string; lead_time_days: number; min_order_yuan: number }> = {
  COKE_CN: { id: "COKE_CN", name: "可口可乐", lead_time_days: 2, min_order_yuan: 500 },
  NONGFU: { id: "NONGFU", name: "农夫山泉", lead_time_days: 1, min_order_yuan: 300 },
  MASTER_KONG: { id: "MASTER_KONG", name: "康师傅", lead_time_days: 2, min_order_yuan: 600 },
  NESTLE: { id: "NESTLE", name: "雀巢", lead_time_days: 3, min_order_yuan: 800 },
};

const MACHINES: Record<string, { id: string; name: string; location: string }> = {
  "vm-001": { id: "vm-001", name: "1楼大厅", location: "1F Main Hall" },
  "vm-002": { id: "vm-002", name: "2楼会议区", location: "2F Conference Wing" },
  "vm-003": { id: "vm-003", name: "地下停车场", location: "B1 Parking" },
};


interface Sku {
  sku_id: string;
  name: string;
  cost_fen: number;
  retail_fen: number;
  moq: number;
  supplier_id: string;
}

const SKUS: Record<string, Sku> = {
  "CC-001": { sku_id: "CC-001", name: "无糖可乐 330ml", cost_fen: 180, retail_fen: 350, moq: 24, supplier_id: "COKE_CN" },
  "CC-002": { sku_id: "CC-002", name: "雪碧无糖 330ml", cost_fen: 180, retail_fen: 350, moq: 24, supplier_id: "COKE_CN" },
  "CC-003": { sku_id: "CC-003", name: "零卡可乐 500ml", cost_fen: 220, retail_fen: 450, moq: 12, supplier_id: "COKE_CN" },
  "NF-001": { sku_id: "NF-001", name: "矿泉水 550ml", cost_fen: 90, retail_fen: 200, moq: 48, supplier_id: "NONGFU" },
  "NF-002": { sku_id: "NF-002", name: "东方树叶绿茶 500ml", cost_fen: 220, retail_fen: 500, moq: 12, supplier_id: "NONGFU" },
  "NF-003": { sku_id: "NF-003", name: "苏打气泡水 330ml", cost_fen: 200, retail_fen: 400, moq: 24, supplier_id: "NONGFU" },
  "NF-004": { sku_id: "NF-004", name: "17.5° NFC橙汁 900ml", cost_fen: 950, retail_fen: 1800, moq: 6, supplier_id: "NONGFU" },
  "MK-001": { sku_id: "MK-001", name: "冰红茶 500ml", cost_fen: 160, retail_fen: 350, moq: 24, supplier_id: "MASTER_KONG" },
  "MK-002": { sku_id: "MK-002", name: "茉莉蜜茶 500ml", cost_fen: 160, retail_fen: 350, moq: 24, supplier_id: "MASTER_KONG" },
  "MK-003": { sku_id: "MK-003", name: "矿物质水 550ml", cost_fen: 80, retail_fen: 200, moq: 48, supplier_id: "MASTER_KONG" },
  "NE-001": { sku_id: "NE-001", name: "雀巢拿铁咖啡 268ml", cost_fen: 600, retail_fen: 1200, moq: 12, supplier_id: "NESTLE" },
  "NE-002": { sku_id: "NE-002", name: "雀巢美式黑咖啡 268ml", cost_fen: 600, retail_fen: 1200, moq: 12, supplier_id: "NESTLE" },
  "NE-003": { sku_id: "NE-003", name: "特趣牛奶巧克力 40g", cost_fen: 380, retail_fen: 800, moq: 6, supplier_id: "NESTLE" },
};

interface BaseLane {
  lane_id: string;
  sku_id: string;
  name: string;
  base_qty: number;
  min_qty: number;
  capacity: number;
  location: string;
}

const BASE_INVENTORY: BaseLane[] = [
  { lane_id: "lane-001", sku_id: "CC-001", name: "无糖可乐 330ml", base_qty: 8, min_qty: 5, capacity: 15, location: "A1" },
  { lane_id: "lane-002", sku_id: "NF-001", name: "矿泉水 550ml", base_qty: 12, min_qty: 8, capacity: 20, location: "A2" },
  { lane_id: "lane-003", sku_id: "MK-001", name: "冰红茶 500ml", base_qty: 0, min_qty: 5, capacity: 15, location: "A3" },
  { lane_id: "lane-004", sku_id: "NF-002", name: "东方树叶绿茶 500ml", base_qty: 3, min_qty: 4, capacity: 12, location: "B1" },
  { lane_id: "lane-005", sku_id: "NE-001", name: "雀巢拿铁咖啡", base_qty: 6, min_qty: 3, capacity: 10, location: "B2" },
  { lane_id: "lane-006", sku_id: "NF-003", name: "苏打气泡水 330ml", base_qty: 0, min_qty: 5, capacity: 15, location: "B3" },
  { lane_id: "lane-007", sku_id: "CC-002", name: "雪碧无糖 330ml", base_qty: 9, min_qty: 5, capacity: 15, location: "C1" },
  { lane_id: "lane-008", sku_id: "MK-002", name: "茉莉蜜茶 500ml", base_qty: 2, min_qty: 5, capacity: 15, location: "C2" },
  { lane_id: "lane-009", sku_id: "NE-002", name: "雀巢美式黑咖啡", base_qty: 5, min_qty: 3, capacity: 10, location: "C3" },
  { lane_id: "lane-010", sku_id: "NF-004", name: "17.5° NFC橙汁", base_qty: 4, min_qty: 2, capacity: 8, location: "D1" },
];

const PO_STATUSES = ["draft", "submitted", "acknowledged", "shipped", "received", "stocked"];

function nowIso(): string {
  return new Date().toISOString();
}

function uid(): string {
  return crypto.randomUUID().replace(/-/g, "").slice(0, 12);
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
    },
  });
}

function notFound(msg = "Not found"): Response {
  return json({ error: msg }, 404);
}

function badRequest(msg: string): Response {
  return json({ error: msg }, 400);
}

async function getInventoryAll(kv: KVNamespace, machineId: string): Promise<unknown[]> {
  const list = await kv.list({ prefix: `inv:${machineId}:` });
  const liveMap: Record<string, { qty: number; last_restocked_at: string }> = {};
  await Promise.all(
    list.keys.map(async (k) => {
      const val = await kv.get(k.name);
      // key is inv:{machineId}:{lane_id}, extract lane_id
      const laneId = k.name.slice(`inv:${machineId}:`.length);
      if (val) liveMap[laneId] = JSON.parse(val);
    })
  );
  return BASE_INVENTORY.map((lane) => {
    const live = liveMap[lane.lane_id];
    const qty = live ? live.qty : 0;
    const last_restocked_at = live ? live.last_restocked_at : null;
    return {
      ...lane,
      qty,
      last_restocked_at,
      low_stock: qty <= lane.min_qty,
    };
  });
}

async function getInventoryOne(kv: KVNamespace, machineId: string, lane_id: string): Promise<unknown | null> {
  const base = BASE_INVENTORY.find((l) => l.lane_id === lane_id);
  if (!base) return null;
  const val = await kv.get(`inv:${machineId}:${lane_id}`);
  const live = val ? JSON.parse(val) : { qty: 0, last_restocked_at: null };
  return { ...base, qty: live.qty, last_restocked_at: live.last_restocked_at, low_stock: live.qty <= base.min_qty };
}

async function getAllPOs(kv: KVNamespace): Promise<unknown[]> {
  const list = await kv.list({ prefix: "po:" });
  const items = await Promise.all(list.keys.map((k) => kv.get(k.name)));
  return items.filter(Boolean).map((v) => JSON.parse(v!));
}

async function getAllPreorders(kv: KVNamespace): Promise<unknown[]> {
  const list = await kv.list({ prefix: "pre:" });
  const items = await Promise.all(list.keys.map((k) => kv.get(k.name)));
  return items.filter(Boolean).map((v) => JSON.parse(v!));
}

async function stockPO(kv: KVNamespace, machineId: string, po: any): Promise<void> {
  const now = nowIso();
  for (const item of po.items) {
    const lanes = BASE_INVENTORY.filter((l) => l.sku_id === item.sku_id);
    for (const lane of lanes) {
      const val = await kv.get(`inv:${machineId}:${lane.lane_id}`);
      const live = val ? JSON.parse(val) : { qty: 0, last_restocked_at: now };
      const newQty = Math.min(live.qty + item.qty, lane.capacity);
      await kv.put(`inv:${machineId}:${lane.lane_id}`, JSON.stringify({ qty: newQty, last_restocked_at: now }));
    }
    const allPre = await getAllPreorders(kv);
    const matching = (allPre as any[]).filter(
      (p: any) => p.sku_id === item.sku_id && p.status === "pending"
    );
    for (const pre of matching) {
      pre.status = "stock_arrived";
      pre.updated_at = now;
      await kv.put(`pre:${pre.id}`, JSON.stringify(pre));
    }
  }
}

async function getMachineStats(kv: KVNamespace, machineId: string): Promise<{ total_items: number; low_stock_count: number }> {
  const inv = (await getInventoryAll(kv, machineId)) as any[];
  const total_items = inv.reduce((s: number, l: any) => s + (l.qty || 0), 0);
  const low_stock_count = inv.filter((l: any) => l.low_stock).length;
  return { total_items, low_stock_count };
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

  const url = new URL(request.url);
  const path = url.pathname.replace(/\/$/, "") || "/";
  const method = request.method;
  const kv = env.SC_KV;

  if (method === "GET" && path === "/health") {
    return json({ status: "ok", time: nowIso() });
  }

  // ── Machines endpoints ─────────────────────────────────────────────────────
  if (method === "GET" && path === "/machines") {
    const machineList = await Promise.all(
      Object.values(MACHINES).map(async (m) => {
        const stats = await getMachineStats(kv, m.id);
        return { ...m, ...stats };
      })
    );
    return json(machineList);
  }

  const machineMatch = path.match(/^\/machines\/([^/]+)$/);
  if (machineMatch && method === "GET") {
    const mid = machineMatch[1];
    const machine = MACHINES[mid];
    if (!machine) return notFound("Machine not found");
    const stats = await getMachineStats(kv, mid);
    return json({ ...machine, ...stats });
  }

  const machineInvMatch = path.match(/^\/machines\/([^/]+)\/inventory$/);
  if (machineInvMatch && method === "GET") {
    const mid = machineInvMatch[1];
    if (!MACHINES[mid]) return notFound("Machine not found");
    const inv = await getInventoryAll(kv, mid);
    return json(inv);
  }

  if (method === "GET" && path === "/suppliers") {
    return json(Object.values(SUPPLIERS));
  }

  const supplierMatch = path.match(/^\/suppliers\/([^/]+)$/);
  if (supplierMatch) {
    const sid = supplierMatch[1];
    if (method === "GET") {
      const supplier = SUPPLIERS[sid];
      if (!supplier) return notFound("Supplier not found");
      return json(supplier);
    }
  }

  const supplierSkuMatch = path.match(/^\/suppliers\/([^/]+)\/skus$/);
  if (supplierSkuMatch && method === "GET") {
    const sid = supplierSkuMatch[1];
    if (!SUPPLIERS[sid]) return notFound("Supplier not found");
    const keyword = url.searchParams.get("keyword") || "";
    let skus = Object.values(SKUS).filter((s) => s.supplier_id === sid);
    if (keyword) {
      const kw = keyword.toLowerCase();
      skus = skus.filter((s) => s.name.toLowerCase().includes(kw) || s.sku_id.toLowerCase().includes(kw));
    }
    return json(skus);
  }

  if (path === "/purchase-orders") {
    if (method === "POST") {
      const body: any = await request.json();
      const { supplier_id, items, note, priority, machine_id } = body;
      if (!supplier_id || !SUPPLIERS[supplier_id]) return badRequest("Invalid supplier_id");
      if (!items || !Array.isArray(items) || items.length === 0) return badRequest("items required");
      for (const item of items) {
        if (!item.sku_id || !SKUS[item.sku_id]) return badRequest(`Invalid sku_id: ${item.sku_id}`);
        if (!item.qty || item.qty < 1) return badRequest("qty must be >= 1");
      }
      const now = nowIso();
      const po = {
        id: `PO-${uid()}`,
        supplier_id,
        supplier_name: SUPPLIERS[supplier_id].name,
        machine_id: machine_id || "vm-001",
        items: items.map((i: any) => ({
          sku_id: i.sku_id,
          sku_name: SKUS[i.sku_id].name,
          qty: i.qty,
          cost_fen: SKUS[i.sku_id].cost_fen,
        })),
        note: note || "",
        priority: priority || "normal",
        status: "draft",
        created_at: now,
        updated_at: now,
        status_history: [{ status: "draft", at: now }],
      };
      await kv.put(`po:${po.id}`, JSON.stringify(po));
      return json(po, 201);
    }

    if (method === "GET") {
      const statusFilter = url.searchParams.get("status");
      const supplierFilter = url.searchParams.get("supplier_id");
      let pos = (await getAllPOs(kv)) as any[];
      if (statusFilter) pos = pos.filter((p) => p.status === statusFilter);
      if (supplierFilter) pos = pos.filter((p) => p.supplier_id === supplierFilter);
      return json(pos);
    }
  }

  const poMatch = path.match(/^\/purchase-orders\/([^/]+)$/);
  if (poMatch) {
    const poId = poMatch[1];
    if (method === "GET") {
      const val = await kv.get(`po:${poId}`);
      if (!val) return notFound("PO not found");
      return json(JSON.parse(val));
    }
  }

  const poAdvanceMatch = path.match(/^\/purchase-orders\/([^/]+)\/advance$/);
  if (poAdvanceMatch && method === "POST") {
    const poId = poAdvanceMatch[1];
    const val = await kv.get(`po:${poId}`);
    if (!val) return notFound("PO not found");
    const po = JSON.parse(val);
    const body: any = await request.json().catch(() => ({}));
    const currentIdx = PO_STATUSES.indexOf(po.status);
    let targetStatus: string;
    if (body.to_status) {
      const targetIdx = PO_STATUSES.indexOf(body.to_status);
      if (targetIdx < 0) return badRequest("Invalid to_status");
      if (targetIdx <= currentIdx) return badRequest("Cannot advance to earlier or same status");
      targetStatus = body.to_status;
    } else {
      if (currentIdx >= PO_STATUSES.length - 1) return badRequest("PO already at final status");
      targetStatus = PO_STATUSES[currentIdx + 1];
    }
    const now = nowIso();
    po.status = targetStatus;
    po.updated_at = now;
    po.status_history.push({ status: targetStatus, at: now });
    if (targetStatus === "stocked") {
      await stockPO(kv, po.machine_id ?? "vm-001", po);
    }
    await kv.put(`po:${poId}`, JSON.stringify(po));
    return json(po);
  }

  if (path === "/inventory") {
    if (method === "GET") {
      const machineId = url.searchParams.get("machine_id") || "vm-001";
      const lowOnly = url.searchParams.get("low_stock_only") === "true";
      let inv = (await getInventoryAll(kv, machineId)) as any[];
      if (lowOnly) inv = inv.filter((i) => i.low_stock);
      return json(inv);
    }
  }

  const invLaneMatch = path.match(/^\/inventory\/([^/]+)$/);
  if (invLaneMatch && method === "GET") {
    const laneId = invLaneMatch[1];
    const machineId = url.searchParams.get("machine_id") || "vm-001";
    const lane = await getInventoryOne(kv, machineId, laneId);
    if (!lane) return notFound("Lane not found");
    return json(lane);
  }

  if (path === "/inventory/restock" && method === "POST") {
    const body: any = await request.json();
    const { po_id, items, machine_id } = body;
    if (!items || !Array.isArray(items)) return badRequest("items required");
    const machineId = machine_id || "vm-001";
    const now = nowIso();
    const results = [];
    for (const item of items) {
      const { lane_id, sku_id, qty } = item;
      const base = BASE_INVENTORY.find((l) => l.lane_id === lane_id);
      if (!base) { results.push({ lane_id, error: "Lane not found" }); continue; }
      if (base.sku_id !== sku_id) { results.push({ lane_id, error: "SKU mismatch" }); continue; }
      const val = await kv.get(`inv:${machineId}:${lane_id}`);
      const live = val ? JSON.parse(val) : { qty: 0, last_restocked_at: now };
      const newQty = Math.min(live.qty + qty, base.capacity);
      await kv.put(`inv:${machineId}:${lane_id}`, JSON.stringify({ qty: newQty, last_restocked_at: now }));
      results.push({ lane_id, sku_id, added: qty, new_qty: newQty });
    }
    return json({ po_id: po_id || null, restocked_at: now, results });
  }

  if (path === "/preorders") {
    if (method === "POST") {
      const body: any = await request.json();
      const { sku_id, sku_name, qty, user_id, note, notify_channel } = body;
      if (!sku_id) return badRequest("sku_id required");
      if (!qty || qty < 1) return badRequest("qty must be >= 1");
      if (!user_id) return badRequest("user_id required");
      const now = nowIso();
      const pre = {
        id: `PRE-${uid()}`,
        sku_id,
        sku_name: sku_name || SKUS[sku_id]?.name || sku_id,
        qty,
        user_id,
        note: note || "",
        notify_channel: notify_channel || "none",
        status: "pending",
        created_at: now,
        updated_at: now,
        notified_at: null,
      };
      await kv.put(`pre:${pre.id}`, JSON.stringify(pre));
      return json(pre, 201);
    }

    if (method === "GET") {
      const statusFilter = url.searchParams.get("status");
      const userFilter = url.searchParams.get("user_id");
      let pres = (await getAllPreorders(kv)) as any[];
      if (statusFilter) pres = pres.filter((p) => p.status === statusFilter);
      if (userFilter) pres = pres.filter((p) => p.user_id === userFilter);
      return json(pres);
    }
  }

  const preMatch = path.match(/^\/preorders\/([^/]+)$/);
  if (preMatch) {
    const preId = preMatch[1];
    if (method === "GET") {
      const val = await kv.get(`pre:${preId}`);
      if (!val) return notFound("Preorder not found");
      return json(JSON.parse(val));
    }
  }

  const preNotifyMatch = path.match(/^\/preorders\/([^/]+)\/notify$/);
  if (preNotifyMatch && method === "POST") {
    const preId = preNotifyMatch[1];
    const val = await kv.get(`pre:${preId}`);
    if (!val) return notFound("Preorder not found");
    const pre = JSON.parse(val);
    const now = nowIso();
    pre.notified_at = now;
    pre.updated_at = now;
    if (pre.status === "stock_arrived") pre.status = "notified";
    await kv.put(`pre:${preId}`, JSON.stringify(pre));
    return json(pre);
  }

  if (path === "/internal/daily-order/preview" && method === "GET") {
    const machineId = url.searchParams.get("machine_id") || "vm-001";
    const inv = (await getInventoryAll(kv, machineId)) as any[];
    const suggestions: any[] = [];
    const bySupplier: Record<string, any[]> = {};
    for (const lane of inv) {
      if (lane.qty < lane.min_qty) {
        const sku = SKUS[lane.sku_id];
        if (!sku) continue;
        const reorderQty = Math.max(sku.moq, lane.capacity - lane.qty);
        const sid = sku.supplier_id;
        if (!bySupplier[sid]) bySupplier[sid] = [];
        bySupplier[sid].push({ lane_id: lane.lane_id, sku_id: lane.sku_id, sku_name: sku.name, current_qty: lane.qty, min_qty: lane.min_qty, reorder_qty: reorderQty, cost_fen: sku.cost_fen * reorderQty });
      }
    }
    for (const [sid, items] of Object.entries(bySupplier)) {
      const total_cost = items.reduce((s, i) => s + i.cost_fen, 0);
      suggestions.push({ supplier_id: sid, supplier_name: SUPPLIERS[sid].name, min_order_yuan: SUPPLIERS[sid].min_order_yuan, total_cost_fen: total_cost, meets_minimum: total_cost >= SUPPLIERS[sid].min_order_yuan * 100, items });
    }
    return json({ preview_at: nowIso(), suggestions });
  }

  if (path === "/internal/daily-order" && method === "POST") {
    const body: any = await request.json().catch(() => ({}));
    const dry_run = body.dry_run === true;
    const machineId = body.machine_id || "vm-001";
    const inv = (await getInventoryAll(kv, machineId)) as any[];
    const bySupplier: Record<string, any[]> = {};
    for (const lane of inv) {
      if (lane.qty < lane.min_qty) {
        const sku = SKUS[lane.sku_id];
        if (!sku) continue;
        const reorderQty = Math.max(sku.moq, lane.capacity - lane.qty);
        const sid = sku.supplier_id;
        if (!bySupplier[sid]) bySupplier[sid] = [];
        const existing = bySupplier[sid].find((i: any) => i.sku_id === lane.sku_id);
        if (existing) { existing.qty += reorderQty; }
        else bySupplier[sid].push({ sku_id: lane.sku_id, qty: reorderQty });
      }
    }
    const created: any[] = [];
    const skipped: any[] = [];
    for (const [sid, items] of Object.entries(bySupplier)) {
      const totalCost = items.reduce((s, i) => s + (SKUS[i.sku_id]?.cost_fen || 0) * i.qty, 0);
      if (totalCost < SUPPLIERS[sid].min_order_yuan * 100) {
        skipped.push({ supplier_id: sid, reason: "below_min_order", total_cost_fen: totalCost });
        continue;
      }
      if (!dry_run) {
        const now = nowIso();
        const po: any = {
          id: `PO-${uid()}`,
          supplier_id: sid,
          supplier_name: SUPPLIERS[sid].name,
          machine_id: machineId,
          items: items.map((i: any) => ({ sku_id: i.sku_id, sku_name: SKUS[i.sku_id]?.name || i.sku_id, qty: i.qty, cost_fen: SKUS[i.sku_id]?.cost_fen || 0 })),
          note: "auto daily order",
          priority: "normal",
          status: "draft",
          created_at: now,
          updated_at: now,
          status_history: [{ status: "draft", at: now }],
        };
        await kv.put(`po:${po.id}`, JSON.stringify(po));
        created.push(po);
      } else {
        created.push({ supplier_id: sid, items, total_cost_fen: totalCost, dry_run: true });
      }
    }
    return json({ dry_run, created_at: nowIso(), created, skipped }, dry_run ? 200 : 201);
  }

  return notFound("Endpoint not found");
}

async function runDailyOrder(kv: KVNamespace): Promise<void> {
  // Run daily order for vm-001 by default (cron job)
  const machineId = "vm-001";
  const inv = (await getInventoryAll(kv, machineId)) as any[];
  const bySupplier: Record<string, any[]> = {};
  for (const lane of inv) {
    if (lane.qty < lane.min_qty) {
      const sku = SKUS[lane.sku_id];
      if (!sku) continue;
      const reorderQty = Math.max(sku.moq, lane.capacity - lane.qty);
      const sid = sku.supplier_id;
      if (!bySupplier[sid]) bySupplier[sid] = [];
      const existing = bySupplier[sid].find((i: any) => i.sku_id === lane.sku_id);
      if (existing) { existing.qty += reorderQty; }
      else bySupplier[sid].push({ sku_id: lane.sku_id, qty: reorderQty });
    }
  }
  for (const [sid, items] of Object.entries(bySupplier)) {
    const totalCost = items.reduce((s, i) => s + (SKUS[i.sku_id]?.cost_fen || 0) * i.qty, 0);
    if (totalCost < SUPPLIERS[sid].min_order_yuan * 100) continue;
    const now = nowIso();
    const po: any = {
      id: `PO-${uid()}`,
      supplier_id: sid,
      supplier_name: SUPPLIERS[sid].name,
      machine_id: machineId,
      items: items.map((i: any) => ({
        sku_id: i.sku_id,
        sku_name: SKUS[i.sku_id]?.name || i.sku_id,
        qty: i.qty,
        cost_fen: SKUS[i.sku_id]?.cost_fen || 0,
      })),
      note: "cron auto daily order",
      priority: "normal",
      status: "draft",
      created_at: now,
      updated_at: now,
      status_history: [{ status: "draft", at: now }],
    };
    await kv.put(`po:${po.id}`, JSON.stringify(po));
    console.log(`[cron] Created PO ${po.id} for supplier ${sid}, ${items.length} SKUs`);
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    return handleRequest(request, env);
  },

  async scheduled(_event: ScheduledEvent, env: Env, _ctx: ExecutionContext): Promise<void> {
    console.log("[cron] Daily auto-order triggered at", nowIso());
    await runDailyOrder(env.SC_KV);
  },
};
