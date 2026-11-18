const canvas = document.getElementById("warehouseCanvas");
const ctx = canvas.getContext("2d");

const state = {
  data: null,
  selectedAgvId: null,
  blockMode: false,
  refreshing: false,
};

const statusText = {
  draft: "草稿",
  allocated: "已预占",
  released: "已释放",
  completed: "已完成",
  cancelled: "已取消",
  failed: "失败",
  pending: "待处理",
  assigned: "已指派",
  running: "运行中",
  idle: "空闲",
  to_pickup: "前往取货",
  loading: "装载中",
  to_dropoff: "前往放货",
  unloading: "卸载中",
  charging: "充电中",
  fault: "故障",
  offline: "离线",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showToast(message, error = false) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className = `toast show${error ? " error" : ""}`;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.className = "toast";
  }, 2800);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new Error(payload?.error?.message || `HTTP ${response.status}`);
  }
  return payload;
}

async function post(path, body = {}) {
  return api(path, { method: "POST", body: JSON.stringify(body) });
}

async function put(path, body = {}) {
  return api(path, { method: "PUT", body: JSON.stringify(body) });
}

async function del(path) {
  return api(path, { method: "DELETE" });
}

async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    state.data = await api("/api/state");
    const indicator = document.getElementById("connection");
    indicator.textContent = state.data.runtime.paused ? "已暂停" : "运行中";
    indicator.className = `status-pill online`;
    render();
  } catch (error) {
    const indicator = document.getElementById("connection");
    indicator.textContent = "连接失败";
    indicator.className = "status-pill offline";
    showToast(error.message, true);
  } finally {
    state.refreshing = false;
  }
}

function render() {
  if (!state.data) return;
  renderMetrics();
  renderAgvDetail();
  renderTasks();
  renderEvents();
  renderTables();
  renderAnalytics();
  populateSelects();
  drawMap();
}

function renderMetrics() {
  const { runtime, analytics, agvs } = state.data;
  document.getElementById("metricTick").textContent = runtime.tick;
  document.getElementById("metricActive").textContent = analytics.tasks_active;
  document.getElementById("metricCompleted").textContent = analytics.tasks_completed;
  document.getElementById("metricAgvs").textContent = agvs.length;
  document.getElementById("metricConflicts").textContent = analytics.counters.conflicts_prevented;
  document.getElementById("metricDeadlocks").textContent = analytics.counters.deadlocks_resolved;
  document.getElementById("speedSelect").value = String(runtime.speed);
}

function renderAgvDetail() {
  const agv = state.data.agvs.find((item) => item.id === state.selectedAgvId);
  const name = document.getElementById("selectedAgvName");
  const detail = document.getElementById("agvDetail");
  if (!agv) {
    name.textContent = "未选择";
    detail.textContent = "点击地图上的车辆查看详情";
    return;
  }
  name.textContent = agv.id;
  detail.innerHTML = `
    <span>状态：${escapeHtml(statusText[agv.status] || agv.status)}</span>
    <span>电量：${Number(agv.battery).toFixed(1)}%</span>
    <span>位置：(${agv.x}, ${agv.y})</span>
    <span>任务：${escapeHtml(agv.active_task_id || "无")}</span>
    <span>载荷：${escapeHtml(agv.payload_sku || "无")}</span>
    <span>等待：${agv.waiting_ticks || 0} tick</span>
  `;
}

function renderTasks() {
  const list = document.getElementById("taskList");
  const statusFilter = document.getElementById("taskStatusFilter").value;
  const agvFilter = document.getElementById("taskAgvFilter").value;
  const priorityFilter = Number(document.getElementById("taskPriorityFilter").value || 0);
  let tasks = state.data.tasks;
  if (statusFilter) tasks = tasks.filter((task) => task.status === statusFilter);
  if (agvFilter) tasks = tasks.filter((task) => task.assigned_agv_id === agvFilter);
  if (priorityFilter) tasks = tasks.filter((task) => Number(task.priority) >= priorityFilter);
  document.getElementById("taskCount").textContent = String(tasks.length);
  if (!tasks.length) {
    list.innerHTML = '<p class="muted">暂无任务</p>';
    return;
  }
  list.innerHTML = tasks
    .slice(0, 30)
    .map(
      (task) => `
      <div class="task-item">
        <div class="task-row">
          <strong>${escapeHtml(task.id)}</strong>
          <span class="status ${escapeHtml(task.status)}">${escapeHtml(statusText[task.status] || task.status)}</span>
        </div>
        <div class="task-meta">
          优先级 ${task.priority} · ${escapeHtml(task.assigned_agv_id || "未指派")} · 数量 ${task.qty}
        </div>
        <div class="mini-actions">
          <button data-task-action="priority" data-id="${escapeHtml(task.id)}">优先级 +1</button>
          <button data-task-action="cancel" data-id="${escapeHtml(task.id)}">取消</button>
          <button data-task-action="reassign" data-id="${escapeHtml(task.id)}">重派</button>
        </div>
      </div>`
    )
    .join("");
}

function renderEvents() {
  const list = document.getElementById("eventList");
  list.innerHTML = state.data.events
    .slice()
    .reverse()
    .slice(0, 30)
    .map(
      (event) => `
      <div class="event-item">
        <div class="task-row">
          <strong>${escapeHtml(event.type)}</strong>
          <span class="event-meta">tick ${event.tick}</span>
        </div>
        <p>${escapeHtml(event.message)}</p>
      </div>`
    )
    .join("");
}

function renderTables() {
  const { skus, locations, inventory, orders } = state.data;
  const orderStatusFilter = document.getElementById("orderStatusFilter").value;
  const inventoryBySku = Object.fromEntries(skus.map((sku) => [sku.id, sku]));
  const locationById = Object.fromEntries(locations.map((loc) => [loc.id, loc]));

  document.getElementById("skuTable").innerHTML = table(
    ["编码", "名称", "批次规则", "操作"],
    skus.map((sku) => [
      sku.code,
      sku.name,
      sku.batch_rule,
      buttons([
        ["edit-sku", sku.id, "编辑"],
        ["delete-sku", sku.id, "删除"],
      ]),
    ]),
    true
  );

  document.getElementById("locationTable").innerHTML = table(
    ["编码", "类型", "坐标", "操作"],
    locations.map((loc) => [
      loc.code,
      loc.type,
      `${loc.x},${loc.y}`,
      buttons([
        ["edit-location", loc.id, "编辑"],
        ["delete-location", loc.id, "删除"],
      ]),
    ]),
    true
  );

  document.getElementById("inventoryTable").innerHTML = table(
    ["SKU", "库位", "数量", "预占", "批次"],
    inventory.map((row) => [
      inventoryBySku[row.sku_id]?.code || row.sku_id,
      locationById[row.location_id]?.code || row.location_id,
      row.qty,
      row.reserved_qty,
      row.batch || "-",
    ])
  );

  const filteredOrders = orderStatusFilter
    ? orders.filter((order) => order.status === orderStatusFilter)
    : orders;
  document.getElementById("orderTable").innerHTML = table(
    ["订单", "状态", "优先级", "明细", "操作"],
    filteredOrders.map((order) => [
      order.code,
      `<span class="status ${escapeHtml(order.status)}">${escapeHtml(statusText[order.status] || order.status)}</span>`,
      order.priority,
      `${order.lines.length} 条`,
      order.status === "draft"
        ? buttons([
            ["release-order", order.id, "释放"],
            ["cancel-order", order.id, "取消"],
          ])
        : "",
    ]),
    true
  );
}

function table(headers, rows, raw = false) {
  if (!rows.length) return '<p class="muted">暂无数据</p>';
  return `<table><thead><tr>${headers
    .map((header) => `<th>${escapeHtml(header)}</th>`)
    .join("")}</tr></thead><tbody>${rows
    .map(
      (row) =>
        `<tr>${row
          .map((cell) => `<td>${raw ? cell : escapeHtml(cell)}</td>`)
          .join("")}</tr>`
    )
    .join("")}</tbody></table>`;
}

function buttons(items) {
  return `<div class="mini-actions">${items
    .map(
      ([action, id, label]) =>
        `<button data-action="${action}" data-id="${escapeHtml(id)}">${escapeHtml(label)}</button>`
    )
    .join("")}</div>`;
}

function renderAnalytics() {
  const analytics = state.data.analytics;
  const statusColors = {
    pending: "#5b7891",
    assigned: "#2f73a4",
    running: "#157a74",
    completed: "#327a4f",
    cancelled: "#b7413e",
    failed: "#b7413e",
  };
  const max = Math.max(1, ...Object.values(analytics.task_status));
  document.getElementById("statusBars").innerHTML = Object.entries(analytics.task_status)
    .map(
      ([key, value]) => `
      <div class="bar-row">
        <span>${escapeHtml(statusText[key] || key)}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${(value / max) * 100}%;background:${statusColors[key] || "#157a74"}"></div></div>
        <strong>${value}</strong>
      </div>`
    )
    .join("") || '<p class="muted">暂无任务</p>';

  document.getElementById("utilizationList").innerHTML = analytics.utilization
    .slice(0, 10)
    .map(
      (row) => `
      <div class="util-row">
        <strong>${escapeHtml(row.agv_id)}</strong>
        <div class="bar-track"><div class="bar-fill" style="width:${Math.min(row.value, 100)}%"></div></div>
        <span>${row.value.toFixed(1)}%</span>
      </div>`
    )
    .join("");

  document.getElementById("bottleneckList").innerHTML = analytics.bottleneck_cells
    .map(
      (row) => `
      <div class="bottleneck-row">
        <strong>(${escapeHtml(row.cell)})</strong>
        <div class="bar-track"><div class="bar-fill" style="width:${Math.min(row.visits * 12, 100)}%;background:#c26b2e"></div></div>
        <span>${row.visits}</span>
      </div>`
    )
    .join("") || '<p class="muted">暂无移动数据</p>';
}

function populateSelects() {
  const { skus, locations, agvs } = state.data;
  for (const id of ["inventorySku", "orderSku"]) {
    const select = document.getElementById(id);
    const current = select.value;
    select.innerHTML = skus
      .map((sku) => `<option value="${escapeHtml(sku.id)}">${escapeHtml(sku.code)} · ${escapeHtml(sku.name)}</option>`)
      .join("");
    if (current && skus.some((sku) => sku.id === current)) select.value = current;
  }
  const inventoryLocation = document.getElementById("inventoryLocation");
  const current = inventoryLocation.value;
  inventoryLocation.innerHTML = locations
    .filter((loc) => loc.type === "shelf")
    .map((loc) => `<option value="${escapeHtml(loc.id)}">${escapeHtml(loc.code)} · (${loc.x},${loc.y})</option>`)
    .join("");
  if (current && locations.some((loc) => loc.id === current)) inventoryLocation.value = current;

  const taskAgvFilter = document.getElementById("taskAgvFilter");
  const selectedAgvFilter = taskAgvFilter.value;
  taskAgvFilter.innerHTML =
    '<option value="">全部 AGV</option>' +
    agvs
      .map((agv) => `<option value="${escapeHtml(agv.id)}">${escapeHtml(agv.id)}</option>`)
      .join("");
  if (selectedAgvFilter && agvs.some((agv) => agv.id === selectedAgvFilter)) {
    taskAgvFilter.value = selectedAgvFilter;
  }
}

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(320, Math.floor(rect.width * dpr));
  const height = Math.max(220, Math.floor(rect.height * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  return { width, height, dpr };
}

function drawMap() {
  if (!state.data) return;
  const { width, height, dpr } = resizeCanvas();
  const map = state.data.map;
  const margin = 18 * dpr;
  const cell = Math.min((width - margin * 2) / map.width, (height - margin * 2) / map.height);
  const offsetX = (width - cell * map.width) / 2;
  const offsetY = (height - cell * map.height) / 2;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#f7f9fa";
  ctx.fillRect(0, 0, width, height);

  const locationMap = new Map(map.locations.map((loc) => [`${loc.x},${loc.y}`, loc]));
  const blocked = new Set(map.blocked_cells.map((cell) => `${cell[0]},${cell[1]}`));
  const colors = {
    floor: "#e9eef2",
    shelf: "#607082",
    pick: "#2f8a69",
    drop: "#c26b2e",
    charger: "#2f73a4",
    blocked: "#b7413e",
  };

  for (let y = 0; y < map.height; y += 1) {
    for (let x = 0; x < map.width; x += 1) {
      const key = `${x},${y}`;
      const loc = locationMap.get(key);
      const type = blocked.has(key) ? "blocked" : loc?.type || "floor";
      ctx.fillStyle = colors[type] || colors.floor;
      ctx.fillRect(
        offsetX + x * cell + dpr,
        offsetY + y * cell + dpr,
        cell - dpr * 2,
        cell - dpr * 2
      );
      ctx.strokeStyle = "rgba(255,255,255,.72)";
      ctx.lineWidth = dpr;
      ctx.strokeRect(offsetX + x * cell, offsetY + y * cell, cell, cell);
      if (loc && ["pick", "drop", "charger"].includes(type)) {
        ctx.fillStyle = "#fff";
        ctx.font = `${Math.max(8, cell * 0.26)}px "Segoe UI"`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(type.slice(0, 1).toUpperCase(), offsetX + (x + 0.5) * cell, offsetY + (y + 0.5) * cell);
      }
    }
  }

  for (const plan of state.data.plans) {
    if (plan.kind !== "task") continue;
    const selected = state.data.tasks.find((task) => task.id === plan.task_id)?.assigned_agv_id === state.selectedAgvId;
    ctx.beginPath();
    plan.steps.forEach((step, index) => {
      const px = offsetX + (step.x + 0.5) * cell;
      const py = offsetY + (step.y + 0.5) * cell;
      if (index === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.strokeStyle = selected ? "#0c6d68" : "rgba(21,122,116,.38)";
    ctx.lineWidth = selected ? 3 * dpr : 1.5 * dpr;
    ctx.stroke();
  }

  for (const agv of state.data.agvs) {
    const px = offsetX + (agv.x + 0.5) * cell;
    const py = offsetY + (agv.y + 0.5) * cell;
    const radius = Math.max(9 * dpr, cell * 0.27);
    ctx.beginPath();
    ctx.arc(px, py, radius, 0, Math.PI * 2);
    ctx.fillStyle = agv.status === "fault" ? "#b7413e" : agv.status === "charging" ? "#2f73a4" : "#17324d";
    ctx.fill();
    ctx.lineWidth = agv.id === state.selectedAgvId ? 3 * dpr : 1.5 * dpr;
    ctx.strokeStyle = agv.id === state.selectedAgvId ? "#e7a33d" : "#fff";
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.font = `600 ${Math.max(8, cell * 0.2)}px "Segoe UI"`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(agv.id.replace("agv-", ""), px, py);
  }
}

function canvasPoint(event) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const xPixel = (event.clientX - rect.left) * dpr;
  const yPixel = (event.clientY - rect.top) * dpr;
  const width = canvas.width;
  const height = canvas.height;
  const margin = 18 * dpr;
  const cell = Math.min((width - margin * 2) / 12, (height - margin * 2) / 8);
  const offsetX = (width - cell * 12) / 2;
  const offsetY = (height - cell * 8) / 2;
  return {
    x: Math.floor((xPixel - offsetX) / cell),
    y: Math.floor((yPixel - offsetY) / cell),
  };
}

async function selectedAgvAction(action) {
  if (!state.selectedAgvId) {
    showToast("请先选择一台 AGV", true);
    return;
  }
  const suffix = { fault: "fault", recover: "recover" }[action];
  try {
    if (action === "battery") {
      await post(`/api/agvs/${state.selectedAgvId}/battery`, { battery: 12 });
      showToast("已将电量调至 12%，空闲车辆会进入充电");
    } else {
      await post(`/api/agvs/${state.selectedAgvId}/${suffix}`);
      showToast("车辆状态已更新");
    }
    await refresh();
  } catch (error) {
    showToast(error.message, true);
  }
}

function bindEvents() {
  document.getElementById("startBtn").addEventListener("click", async () => {
    try { await post("/api/sim/start"); await refresh(); } catch (error) { showToast(error.message, true); }
  });
  document.getElementById("pauseBtn").addEventListener("click", async () => {
    try { await post("/api/sim/pause"); await refresh(); } catch (error) { showToast(error.message, true); }
  });
  document.getElementById("stepBtn").addEventListener("click", async () => {
    try { await post("/api/sim/step", { ticks: 1 }); await refresh(); } catch (error) { showToast(error.message, true); }
  });
  document.getElementById("speedSelect").addEventListener("change", async (event) => {
    try { await post("/api/sim/speed", { speed: Number(event.target.value) }); await refresh(); } catch (error) { showToast(error.message, true); }
  });
  for (const id of ["taskStatusFilter", "taskAgvFilter", "taskPriorityFilter", "orderStatusFilter"]) {
    document.getElementById(id).addEventListener("input", render);
  }
  document.getElementById("resetSimBtn").addEventListener("click", async () => {
    try { await post("/api/sim/reset"); await refresh(); showToast("仿真已重置"); } catch (error) { showToast(error.message, true); }
  });
  document.getElementById("demoOrderBtn").addEventListener("click", async () => {
    try { await post("/api/demo/order"); await refresh(); showToast("演示订单已创建并释放"); } catch (error) { showToast(error.message, true); }
  });
  document.getElementById("deadlockBtn").addEventListener("click", async () => {
    try {
      const result = await post("/api/sim/deadlock");
      showToast(result.message);
      await refresh();
    } catch (error) { showToast(error.message, true); }
  });
  document.getElementById("blockModeBtn").addEventListener("click", () => {
    state.blockMode = !state.blockMode;
    document.getElementById("blockModeBtn").classList.toggle("primary", state.blockMode);
    document.getElementById("blockModeBtn").textContent = state.blockMode ? "封路模式：开" : "选择封路点";
  });
  document.getElementById("faultAgvBtn").addEventListener("click", () => selectedAgvAction("fault"));
  document.getElementById("recoverAgvBtn").addEventListener("click", () => selectedAgvAction("recover"));
  document.getElementById("lowBatteryBtn").addEventListener("click", () => selectedAgvAction("battery"));
  document.getElementById("demoResetBtn").addEventListener("click", async () => {
    if (!window.confirm("确定恢复全部演示数据吗？")) return;
    try { await post("/api/demo/reset"); state.selectedAgvId = null; await refresh(); showToast("演示数据已恢复"); } catch (error) { showToast(error.message, true); }
  });

  canvas.addEventListener("click", async (event) => {
    const { x, y } = canvasPoint(event);
    if (x < 0 || x > 11 || y < 0 || y > 7) return;
    if (state.blockMode) {
      const blocked = state.data.map.blocked_cells.some(([bx, by]) => bx === x && by === y);
      try {
        await post("/api/sim/roadblock", { x, y, blocked: !blocked });
        await refresh();
        showToast(`节点 (${x}, ${y}) ${blocked ? "已解除" : "已封闭"}`);
      } catch (error) { showToast(error.message, true); }
      return;
    }
    const nearby = state.data.agvs
      .map((agv) => ({ agv, distance: Math.abs(agv.x - x) + Math.abs(agv.y - y) }))
      .sort((a, b) => a.distance - b.distance)[0];
    if (nearby && nearby.distance <= 1) {
      state.selectedAgvId = nearby.agv.id;
      renderAgvDetail();
      drawMap();
    }
  });

  document.getElementById("skuForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    try {
      await post("/api/skus", Object.fromEntries(form));
      event.target.reset();
      await refresh();
      showToast("SKU 已保存");
    } catch (error) { showToast(error.message, true); }
  });
  document.getElementById("locationForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = Object.fromEntries(new FormData(event.target));
    form.x = Number(form.x);
    form.y = Number(form.y);
    form.capacity = Number(form.capacity);
    try {
      await post("/api/locations", form);
      event.target.reset();
      await refresh();
      showToast("库位已保存");
    } catch (error) { showToast(error.message, true); }
  });
  document.getElementById("inventoryForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = Object.fromEntries(new FormData(event.target));
    form.qty = Number(form.qty);
    try {
      await post("/api/inventory", form);
      await refresh();
      showToast("库存已登记");
    } catch (error) { showToast(error.message, true); }
  });
  document.getElementById("orderForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = Object.fromEntries(new FormData(event.target));
    const payload = {
      code: form.code,
      requester: form.requester,
      priority: Number(form.priority),
      lines: [{ sku_id: form.sku_id, qty: Number(form.qty) }],
    };
    try {
      await post("/api/orders", payload);
      event.target.reset();
      event.target.elements.code.value = `OUT-${Date.now().toString().slice(-6)}`;
      await refresh();
      showToast("订单已创建");
    } catch (error) { showToast(error.message, true); }
  });

  document.body.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action], button[data-task-action]");
    if (!button) return;
    const id = button.dataset.id;
    try {
      if (button.dataset.taskAction === "priority") {
        const task = state.data.tasks.find((item) => item.id === id);
        await post(`/api/tasks/${id}/priority`, { priority: Math.min(10, task.priority + 1) });
      } else if (button.dataset.taskAction === "cancel") {
        await post(`/api/tasks/${id}/cancel`);
      } else if (button.dataset.taskAction === "reassign") {
        await post(`/api/tasks/${id}/reassign`);
      } else if (button.dataset.action === "release-order") {
        await post(`/api/orders/${id}/release`);
      } else if (button.dataset.action === "cancel-order") {
        await post(`/api/orders/${id}/cancel`);
      } else if (button.dataset.action === "delete-sku") {
        if (!window.confirm("确定删除这个 SKU 吗？")) return;
        await del(`/api/skus/${id}`);
      } else if (button.dataset.action === "delete-location") {
        if (!window.confirm("确定删除这个库位吗？")) return;
        await del(`/api/locations/${id}`);
      } else if (button.dataset.action === "edit-sku") {
        const sku = state.data.skus.find((item) => item.id === id);
        const name = window.prompt("新的 SKU 名称", sku.name);
        if (name === null) return;
        await put(`/api/skus/${id}`, { name });
      } else if (button.dataset.action === "edit-location") {
        const loc = state.data.locations.find((item) => item.id === id);
        const capacity = window.prompt("新的库位容量", loc.capacity);
        if (capacity === null) return;
        await put(`/api/locations/${id}`, { capacity: Number(capacity) });
      }
      await refresh();
    } catch (error) {
      showToast(error.message, true);
    }
  });
}

window.addEventListener("resize", drawMap);
bindEvents();
document.getElementById("orderForm").elements.code.value = `OUT-${Date.now().toString().slice(-6)}`;
refresh();
window.setInterval(refresh, 700);
