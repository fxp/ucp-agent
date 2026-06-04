export interface Env {
  SC_KV: KVNamespace;
}

interface Supplier {
  id: string;
  name: string;
  lead_time_days: number;
  min_order_yuan: number;
}

async function getSupplier(kv: KVNamespace, id: string): Promise<Supplier | null> {
  const v = await kv.get(`supplier:${id}`);
  return v ? JSON.parse(v) : null;
}

async function getAllSuppliers(kv: KVNamespace): Promise<Supplier[]> {
  const list = await kv.list({ prefix: "supplier:" });
  const items = await Promise.all(list.keys.map(k => kv.get(k.name)));
  return items.filter(Boolean).map(v => JSON.parse(v!));
}

interface Machine {
  id: string;
  name: string;
  location: string;
}

async function getMachine(kv: KVNamespace, id: string): Promise<Machine | null> {
  const v = await kv.get(`machine:${id}`);
  return v ? JSON.parse(v) : null;
}

async function getAllMachines(kv: KVNamespace): Promise<Machine[]> {
  const list = await kv.list({ prefix: "machine:" });
  const items = await Promise.all(list.keys.map(k => kv.get(k.name)));
  return items.filter(Boolean).map(v => JSON.parse(v!));
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

async function getSku(kv: KVNamespace, sku_id: string): Promise<Sku | null> {
  const v = await kv.get(`sku:${sku_id}`);
  return v ? JSON.parse(v) : null;
}

async function getAllSkus(kv: KVNamespace): Promise<Sku[]> {
  const list = await kv.list({ prefix: "sku:" });
  const items = await Promise.all(list.keys.map(k => kv.get(k.name)));
  return items.filter(Boolean).map(v => JSON.parse(v!));
}

async function getLane(kv: KVNamespace, machineId: string, laneId: string): Promise<LaneConfig | null> {
  const v = await kv.get(`lane:${machineId}:${laneId}`);
  return v ? JSON.parse(v) : null;
}

async function getAllLanes(kv: KVNamespace, machineId: string): Promise<LaneConfig[]> {
  const list = await kv.list({ prefix: `lane:${machineId}:` });
  const items = await Promise.all(list.keys.map(k => kv.get(k.name)));
  return items.filter(Boolean).map(v => JSON.parse(v!));
}

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
  const lanes = await getAllLanes(kv, machineId);
  return Promise.all(
    lanes.map(async (lane) => {
      const val = await kv.get(`inv:${machineId}:${lane.lane_id}`);
      const live = val ? JSON.parse(val) : { qty: 0, last_restocked_at: null };
      const qty = live.qty ?? 0;
      return {
        ...lane,
        qty,
        last_restocked_at: live.last_restocked_at ?? null,
        low_stock: qty <= lane.min_qty,
      };
    })
  );
}

async function getInventoryOne(kv: KVNamespace, machineId: string, lane_id: string): Promise<unknown | null> {
  const lane = await getLane(kv, machineId, lane_id);
  if (!lane) return null;
  const val = await kv.get(`inv:${machineId}:${lane_id}`);
  const live = val ? JSON.parse(val) : { qty: 0, last_restocked_at: null };
  return { ...lane, qty: live.qty ?? 0, last_restocked_at: live.last_restocked_at ?? null, low_stock: (live.qty ?? 0) <= lane.min_qty };
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
    // Find all lanes for this machine that carry this SKU
    const lanes = await getAllLanes(kv, machineId);
    const matchingLanes = lanes.filter((l) => l.sku_id === item.sku_id);
    for (const lane of matchingLanes) {
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

  // ── SKU CRUD ───────────────────────────────────────────────────────────────────
  if (path === "/skus") {
    if (method === "GET") {
      return json(await getAllSkus(kv));
    }
    if (method === "POST") {
      const body: any = await request.json();
      const { sku_id, name, cost_fen, retail_fen, moq, supplier_id } = body;
      if (!sku_id) return badRequest("sku_id required");
      if (!name) return badRequest("name required");
      if (typeof cost_fen !== "number") return badRequest("cost_fen required (number)");
      if (typeof retail_fen !== "number") return badRequest("retail_fen required (number)");
      if (typeof moq !== "number") return badRequest("moq required (number)");
      if (!supplier_id) return badRequest("supplier_id required");
      const sku: Sku = { sku_id, name, cost_fen, retail_fen, moq, supplier_id };
      await kv.put(`sku:${sku_id}`, JSON.stringify(sku));
      return json(sku, 201);
    }
  }

  const skuMatch = path.match(/^\/skus\/([^/]+)$/);
  if (skuMatch) {
    const skuId = skuMatch[1];
    if (method === "GET") {
      const sku = await getSku(kv, skuId);
      if (!sku) return notFound("SKU not found");
      return json(sku);
    }
    if (method === "PUT") {
      const existing = await getSku(kv, skuId);
      if (!existing) return notFound("SKU not found");
      const body: any = await request.json();
      const updated: Sku = { ...existing, ...body, sku_id: skuId };
      await kv.put(`sku:${skuId}`, JSON.stringify(updated));
      return json(updated);
    }
    if (method === "DELETE") {
      await kv.delete(`sku:${skuId}`);
      return json({ deleted: true, sku_id: skuId });
    }
  }

  // ── Machines endpoints ─────────────────────────────────────────────────────
  if (path === "/machines") {
    if (method === "GET") {
      const machines = await getAllMachines(kv);
      const machineList = await Promise.all(machines.map(async (m) => {
        const stats = await getMachineStats(kv, m.id);
        return { ...m, ...stats };
      }));
      return json(machineList);
    }
    if (method === "POST") {
      const body: any = await request.json();
      const { id, name, location } = body;
      if (!id) return badRequest("id required");
      if (!name) return badRequest("name required");
      const machine: Machine = { id, name, location: location || "" };
      await kv.put(`machine:${id}`, JSON.stringify(machine));
      return json(machine, 201);
    }
  }

  const machineMatch = path.match(/^\/machines\/([^/]+)$/);
  if (machineMatch) {
    const mid = machineMatch[1];
    if (method === "GET") {
      const machine = await getMachine(kv, mid);
      if (!machine) return notFound("Machine not found");
      const stats = await getMachineStats(kv, mid);
      const lanes = await getAllLanes(kv, mid);
      return json({ ...machine, ...stats, lanes });
    }
    if (method === "PUT") {
      const existing = await getMachine(kv, mid);
      if (!existing) return notFound("Machine not found");
      const body: any = await request.json();
      const updated: Machine = { ...existing, ...body, id: mid };
      await kv.put(`machine:${mid}`, JSON.stringify(updated));
      return json(updated);
    }
    if (method === "DELETE") {
      await kv.delete(`machine:${mid}`);
      return json({ deleted: true, id: mid });
    }
  }

  // ── Lane config CRUD ───────────────────────────────────────────────────────
  const machineLanesMatch = path.match(/^\/machines\/([^/]+)\/lanes$/);
  if (machineLanesMatch) {
    const mid = machineLanesMatch[1];
    const lanesMachine = await getMachine(kv, mid);
    if (!lanesMachine) return notFound("Machine not found");
    if (method === "GET") {
      return json(await getAllLanes(kv, mid));
    }
    if (method === "POST") {
      const body: any = await request.json();
      const { lane_id, sku_id, name, price_fen, currency, min_qty, capacity, location } = body;
      if (!lane_id) return badRequest("lane_id required");
      if (!sku_id) return badRequest("sku_id required");
      if (!name) return badRequest("name required");
      if (typeof price_fen !== "number") return badRequest("price_fen required (number)");
      if (typeof min_qty !== "number") return badRequest("min_qty required (number)");
      if (typeof capacity !== "number") return badRequest("capacity required (number)");
      // Validate SKU exists
      const sku = await getSku(kv, sku_id);
      if (!sku) return badRequest(`sku_not_found: ${sku_id}`);
      const lane: LaneConfig = {
        lane_id, machine_id: mid, sku_id, name,
        price_fen, currency: currency || "CNY",
        min_qty, capacity, location: location || "",
      };
      await kv.put(`lane:${mid}:${lane_id}`, JSON.stringify(lane));
      // Initialize inventory entry
      const existingInv = await kv.get(`inv:${mid}:${lane_id}`);
      if (!existingInv) {
        await kv.put(`inv:${mid}:${lane_id}`, JSON.stringify({ qty: 0, last_restocked_at: null }));
      }
      return json(lane, 201);
    }
  }

  const machineLaneMatch = path.match(/^\/machines\/([^/]+)\/lanes\/([^/]+)$/);
  if (machineLaneMatch) {
    const mid = machineLaneMatch[1];
    const laneId = machineLaneMatch[2];
    if (!await getMachine(kv, mid)) return notFound("Machine not found");
    if (method === "PUT") {
      const existing = await getLane(kv, mid, laneId);
      if (!existing) return notFound("Lane not found");
      const body: any = await request.json();
      // Validate SKU if being changed
      if (body.sku_id && body.sku_id !== existing.sku_id) {
        const sku = await getSku(kv, body.sku_id);
        if (!sku) return badRequest(`sku_not_found: ${body.sku_id}`);
      }
      const updated: LaneConfig = { ...existing, ...body, lane_id: laneId, machine_id: mid };
      await kv.put(`lane:${mid}:${laneId}`, JSON.stringify(updated));
      return json(updated);
    }
    if (method === "DELETE") {
      await kv.delete(`lane:${mid}:${laneId}`);
      await kv.delete(`inv:${mid}:${laneId}`);
      return json({ deleted: true, machine_id: mid, lane_id: laneId });
    }
  }

  const machineInvMatch = path.match(/^\/machines\/([^/]+)\/inventory$/);
  if (machineInvMatch && method === "GET") {
    const mid = machineInvMatch[1];
    if (!await getMachine(kv, mid)) return notFound("Machine not found");
    const inv = await getInventoryAll(kv, mid);
    return json(inv);
  }

  if (path === "/suppliers") {
    if (method === "GET") return json(await getAllSuppliers(kv));
    if (method === "POST") {
      const body: any = await request.json();
      const { id, name, lead_time_days, min_order_yuan } = body;
      if (!id) return badRequest("id required");
      if (!name) return badRequest("name required");
      if (typeof lead_time_days !== "number") return badRequest("lead_time_days required (number)");
      if (typeof min_order_yuan !== "number") return badRequest("min_order_yuan required (number)");
      const supplier: Supplier = { id, name, lead_time_days, min_order_yuan };
      await kv.put(`supplier:${id}`, JSON.stringify(supplier));
      return json(supplier, 201);
    }
  }

  const supplierMatch = path.match(/^\/suppliers\/([^/]+)$/);
  if (supplierMatch) {
    const sid = supplierMatch[1];
    if (method === "GET") {
      const supplier = await getSupplier(kv, sid);
      if (!supplier) return notFound("Supplier not found");
      return json(supplier);
    }
    if (method === "PUT") {
      const existing = await getSupplier(kv, sid);
      if (!existing) return notFound("Supplier not found");
      const body: any = await request.json();
      const updated: Supplier = { ...existing, ...body, id: sid };
      await kv.put(`supplier:${sid}`, JSON.stringify(updated));
      return json(updated);
    }
    if (method === "DELETE") {
      await kv.delete(`supplier:${sid}`);
      return json({ deleted: true, id: sid });
    }
  }

  const supplierSkuMatch = path.match(/^\/suppliers\/([^/]+)\/skus$/);
  if (supplierSkuMatch && method === "GET") {
    const sid = supplierSkuMatch[1];
    const supplier = await getSupplier(kv, sid);
    if (!supplier) return notFound("Supplier not found");
    const keyword = url.searchParams.get("keyword") || "";
    let skus = (await getAllSkus(kv)).filter((s) => s.supplier_id === sid);
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
      if (!supplier_id) return badRequest("supplier_id required");
      const poSupplier = await getSupplier(kv, supplier_id);
      if (!poSupplier) return badRequest("supplier_not_found: " + supplier_id);
      if (!items || !Array.isArray(items) || items.length === 0) return badRequest("items required");
      const resolvedItems: any[] = [];
      for (const item of items) {
        if (!item.sku_id) return badRequest("sku_id required in items");
        const sku = await getSku(kv, item.sku_id);
        if (!sku) return badRequest(`sku_not_found: ${item.sku_id}`);
        if (!item.qty || item.qty < 1) return badRequest("qty must be >= 1");
        resolvedItems.push({ sku_id: item.sku_id, sku_name: sku.name, qty: item.qty, cost_fen: sku.cost_fen });
      }
      const now = nowIso();
      const po = {
        id: `PO-${uid()}`,
        supplier_id,
        supplier_name: poSupplier.name,
        machine_id: machine_id || "vm-001",
        items: resolvedItems,
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
      const laneConfig = await getLane(kv, machineId, lane_id);
      if (!laneConfig) { results.push({ lane_id, error: "Lane not found" }); continue; }
      if (laneConfig.sku_id !== sku_id) { results.push({ lane_id, error: "SKU mismatch" }); continue; }
      const val = await kv.get(`inv:${machineId}:${lane_id}`);
      const live = val ? JSON.parse(val) : { qty: 0, last_restocked_at: now };
      const newQty = Math.min(live.qty + qty, laneConfig.capacity);
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
      // Look up sku name from KV if not provided
      let resolvedSkuName = sku_name;
      if (!resolvedSkuName) {
        const sku = await getSku(kv, sku_id);
        resolvedSkuName = sku?.name || sku_id;
      }
      const now = nowIso();
      const pre = {
        id: `PRE-${uid()}`,
        sku_id,
        sku_name: resolvedSkuName,
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
    const bySupplier: Record<string, any[]> = {};
    for (const lane of inv) {
      if (lane.qty < lane.min_qty) {
        const sku = await getSku(kv, lane.sku_id);
        if (!sku) continue;
        const reorderQty = Math.max(sku.moq, lane.capacity - lane.qty);
        const sid = sku.supplier_id;
        if (!bySupplier[sid]) bySupplier[sid] = [];
        bySupplier[sid].push({ lane_id: lane.lane_id, sku_id: lane.sku_id, sku_name: sku.name, current_qty: lane.qty, min_qty: lane.min_qty, reorder_qty: reorderQty, cost_fen: sku.cost_fen * reorderQty });
      }
    }
    const suggestions: any[] = [];
    for (const [sid, items] of Object.entries(bySupplier)) {
      const supplier = await getSupplier(kv, sid);
      if (!supplier) continue;
      const total_cost = items.reduce((s, i) => s + i.cost_fen, 0);
      suggestions.push({ supplier_id: sid, supplier_name: supplier.name, min_order_yuan: supplier.min_order_yuan, total_cost_fen: total_cost, meets_minimum: total_cost >= supplier.min_order_yuan * 100, items });
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
        const sku = await getSku(kv, lane.sku_id);
        if (!sku) continue;
        const reorderQty = Math.max(sku.moq, lane.capacity - lane.qty);
        const sid = sku.supplier_id;
        if (!bySupplier[sid]) bySupplier[sid] = [];
        const existing = bySupplier[sid].find((i: any) => i.sku_id === lane.sku_id);
        if (existing) { existing.qty += reorderQty; existing.sku = sku; }
        else bySupplier[sid].push({ sku_id: lane.sku_id, qty: reorderQty, sku });
      }
    }
    const created: any[] = [];
    const skipped: any[] = [];
    for (const [sid, items] of Object.entries(bySupplier)) {
      const supplier = await getSupplier(kv, sid);
      if (!supplier) continue;
      const totalCost = items.reduce((s, i) => s + (i.sku?.cost_fen || 0) * i.qty, 0);
      if (totalCost < supplier.min_order_yuan * 100) {
        skipped.push({ supplier_id: sid, reason: "below_min_order", total_cost_fen: totalCost });
        continue;
      }
      if (!dry_run) {
        const now = nowIso();
        const po: any = {
          id: `PO-${uid()}`,
          supplier_id: sid,
          supplier_name: supplier.name,
          machine_id: machineId,
          items: items.map((i: any) => ({ sku_id: i.sku_id, sku_name: i.sku?.name || i.sku_id, qty: i.qty, cost_fen: i.sku?.cost_fen || 0 })),
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
        created.push({ supplier_id: sid, items: items.map(i => ({ sku_id: i.sku_id, qty: i.qty })), total_cost_fen: totalCost, dry_run: true });
      }
    }
    return json({ dry_run, created_at: nowIso(), created, skipped }, dry_run ? 200 : 201);
  }

  return notFound("Endpoint not found");
}

async function runDailyOrder(kv: KVNamespace): Promise<void> {
  const machineId = "vm-001";
  const inv = (await getInventoryAll(kv, machineId)) as any[];
  const bySupplier: Record<string, any[]> = {};
  for (const lane of inv) {
    if (lane.qty < lane.min_qty) {
      const sku = await getSku(kv, lane.sku_id);
      if (!sku) continue;
      const reorderQty = Math.max(sku.moq, lane.capacity - lane.qty);
      const sid = sku.supplier_id;
      if (!bySupplier[sid]) bySupplier[sid] = [];
      const existing = bySupplier[sid].find((i: any) => i.sku_id === lane.sku_id);
      if (existing) { existing.qty += reorderQty; existing.sku = sku; }
      else bySupplier[sid].push({ sku_id: lane.sku_id, qty: reorderQty, sku });
    }
  }
  for (const [sid, items] of Object.entries(bySupplier)) {
    const supplier = await getSupplier(kv, sid);
    if (!supplier) continue;
    const totalCost = items.reduce((s, i) => s + (i.sku?.cost_fen || 0) * i.qty, 0);
    if (totalCost < supplier.min_order_yuan * 100) continue;
    const now = nowIso();
    const po: any = {
      id: `PO-${uid()}`,
      supplier_id: sid,
      supplier_name: supplier.name,
      machine_id: machineId,
      items: items.map((i: any) => ({
        sku_id: i.sku_id,
        sku_name: i.sku?.name || i.sku_id,
        qty: i.qty,
        cost_fen: i.sku?.cost_fen || 0,
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
