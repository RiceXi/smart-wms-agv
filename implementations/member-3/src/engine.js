import { readFileSync } from 'node:fs';
import { performance } from 'node:perf_hooks';
import { Store } from './db.js';
import { DomainError, requireValue, integer, textValue, enumValue, isoDate, deterministicId, cell, distance, clone, cyclesOf } from './common.js';
import { buildMap, rebuildEdges, coordinates, mapIndex, neighbors } from './map-engine.js';
import { ReservationTable, findPath } from './planner.js';

export const defaultConfig = JSON.parse(readFileSync(new URL('../scenarios/default-scenario.json', import.meta.url), 'utf8'));
const terminal = ['completed', 'cancelled', 'failed'];
const locationTypes = ['floor', 'shelf', 'pick', 'drop', 'charger', 'blocked'];

export class Engine {
  constructor(options = {}) {
    this.store = options.store ?? new Store(options.filename);
    this.recording = options.recording !== false;
    this.config = { ...defaultConfig, ...options.config };
    this.state = this.store.load();
    if (!this.state) this.initialize(options.seed ?? this.config.seed);
    this.state.running = false;
    this.pendingEvents = [];
    this.rebuildReservations();
  }
  now() { return new Date(Date.parse(this.config.epoch) + this.state.tick * 1000).toISOString(); }
  id() { return deterministicId(this.state.seed, ++this.state.sequence); }
  entity(fields) { return { id: this.id(), created_at: this.now(), updated_at: this.now(), version: 1, ...fields }; }
  touch(entity) { entity.version++; entity.updated_at = this.now(); return entity; }
  get(collection, id) {
    const entity = this.state[collection].find(item => item.id === id);
    requireValue(entity, 'not_found', `找不到${collection}记录：${id}`);
    return entity;
  }
  event(type, aggregateId, payload = {}) {
    const event = { id: this.id(), type, occurredAt: this.now(), tick: this.state.tick, aggregateId, payload };
    this.state.events.push(event);
    if (this.state.events.length > 200) this.state.events.shift();
    this.pendingEvents?.push(event);
    return event;
  }
  initialize(seed) {
    integer(seed, 'seed', 0, 4294967295);
    this.pendingEvents = [];
    this.state = {
      seed, sequence: 0, tick: 0, running: false, speed: 1,
      skus: [], locations: [], inventory: [], ledger: [], orders: [], tasks: [], agvs: [], maps: [], events: [],
      waitGraph: {}, visits: {}, metrics: { conflicts: 0, deadlocks: 0, recoveries: 0, dispatchMs: [] }
    };
    this.state.map = buildMap(this.config, this.id(), this.id());
    for (const [x, y, type, code, capacity] of [
      [4, 3, 'pick', 'PICK-A', 10000], [9, 3, 'pick', 'PICK-B', 10000], [14, 3, 'pick', 'PICK-C', 10000],
      [4, 13, 'drop', 'DROP-A', 10000], [14, 13, 'drop', 'DROP-B', 10000],
      [2, 14, 'charger', 'CHARGE-A', 1], [3, 14, 'charger', 'CHARGE-B', 1],
      [5, 4, 'shelf', 'SHELF-A', 10000], [10, 4, 'shelf', 'SHELF-B', 10000]
    ]) {
      const node = this.state.map.nodes.find(item => item.x === x && item.y === y);
      if (!node) continue;
      Object.assign(node, { type, capacity, walkable: !['shelf', 'blocked'].includes(type) });
      this.state.locations.push(this.entity({ code, zone: '主仓', x, y, type, capacity, map_version: this.state.map.version }));
    }
    rebuildEdges(this.state.map);
    this.state.maps.push(clone(this.state.map));
    for (let index = 0; index < this.config.skus.length; index++) {
      const sku = this.entity(this.config.skus[index]);
      this.state.skus.push(sku);
      const stock = this.entity({ sku_id: sku.id, location_id: this.state.locations[index].id, qty: 5000, reserved_qty: 0, batch: 'INIT-20261119', expire_at: '2030-12-31T00:00:00.000Z' });
      this.state.inventory.push(stock);
      this.state.ledger.push(this.entity({ inventory_id: stock.id, order_id: null, tick: 0, type: 'receive', qty_delta: 5000, reserved_delta: 0 }));
    }
    for (let index = 0; index < this.config.agv_count; index++) {
      const home = { x: 1 + index % (this.config.width - 2), y: 1 + Math.floor(index / (this.config.width - 2)) };
      this.state.agvs.push(this.entity({ code: `AGV-${String(index + 1).padStart(2, '0')}`, map_id: this.state.map.id,
        status: 'idle', ...home, home, battery: 80 + ((seed + index * 7) % 20), active_task_id: null, payload_sku: null,
        last_tick: 0, fault_code: null, path: [], history: [{ ...home, tick: 0 }], plan_version: 0,
        busy_ticks: 0, completed_count: 0, wait_ticks: 0, retreat_count: 0, charging_target: null }));
    }
    this.event('simulation.reset', this.state.map.id, { seed });
    this.rebuildReservations();
    this.persist();
  }
  persist(frame = true) {
    this.store.save(this.state, this.pendingEvents, this.recording && frame ? this.frame() : null);
    this.pendingEvents = [];
  }
  frame() {
    const { tick, seed, speed, map, agvs, tasks, orders, inventory, events } = this.state;
    return clone({ tick, seed, speed, map_version: map.version, agvs, tasks, orders, inventory, events, stats: this.stats() });
  }
  snapshot() { return clone({ ...this.state, maps: undefined, stats: this.stats(), reservations: this.reservations.entries() }); }
  replay() {
    const frames = this.store.frames();
    return { seed: this.state.seed, frames: frames.map(frame => ({ ...frame, map: this.state.maps.find(map => map.version === frame.map_version) })) };
  }
  stats() {
    const state = this.state;
    const completed = state.tasks.filter(task => task.status === 'completed');
    return {
      orders: state.orders.length, tasks: state.tasks.length, agvs: state.agvs.length,
      completed: completed.length, cancelled: state.tasks.filter(task => task.status === 'cancelled').length,
      effectiveTasks: state.tasks.filter(task => !['cancelled', 'failed'].includes(task.status)).length,
      throughput: state.tick ? completed.length * 3600 / state.tick : 0,
      averageTaskTicks: completed.length ? completed.reduce((sum, task) => sum + task.completed_tick - task.created_tick, 0) / completed.length : 0,
      conflicts: state.metrics.conflicts, deadlocks: state.metrics.deadlocks, recoveries: state.metrics.recoveries,
      utilization: state.agvs.map(agv => ({ id: agv.id, code: agv.code, utilization: state.tick ? agv.busy_ticks / state.tick : 0, completed: agv.completed_count })),
      bottlenecks: Object.entries(state.visits).map(([cell, count]) => ({ cell, count })).sort((left, right) => right.count - left.count || left.cell.localeCompare(right.cell)).slice(0, 8)
    };
  }
  unique(collection, code, ignoreId) {
    requireValue(!this.state[collection].some(item => item.code === code && item.id !== ignoreId), 'duplicate_code', `编码 ${code} 已存在`);
  }
  checkVersion(entity, version) {
    if (version !== undefined) requireValue(version === entity.version, 'stale_version', '记录已更新，请刷新后重试');
  }
  sku(input, update = false) {
    const previous = update ? this.get('skus', input.id) : null;
    if (previous) this.checkVersion(previous, input.version);
    const data = { ...previous, ...input };
    const fields = { code: textValue(data.code, 'SKU 编码'), name: textValue(data.name, '商品名称'),
      spec: data.spec === undefined ? '' : String(data.spec).slice(0, 200), batch_rule: enumValue(data.batch_rule ?? 'none', ['none', 'fifo', 'fefo'], '批次规则') };
    this.unique('skus', fields.code, previous?.id);
    if (previous) { Object.assign(previous, fields); this.touch(previous); }
    else this.state.skus.push(this.entity(fields));
    return previous ?? this.state.skus.at(-1);
  }
  deleteSku(id) {
    this.get('skus', id);
    requireValue(!this.state.inventory.some(stock => stock.sku_id === id) && !this.state.orders.some(order => order.lines.some(line => line.sku_id === id)), 'sku_in_use', '商品被库存或订单引用，不能删除');
    this.state.skus = this.state.skus.filter(sku => sku.id !== id);
    return { id };
  }
  newMap(mutator) {
    const previous = this.state.map;
    const updated = clone(this.state.map);
    updated.version = this.id();
    mutator(updated);
    rebuildEdges(updated);
    this.state.map = updated;
    this.state.maps.push(clone(updated));
    for (const location of this.state.locations) { location.map_version = updated.version; this.touch(location); }
    this.event('map.version.changed', updated.id, { map_version: updated.version });
    this.replanMapChanges(previous);
  }
  location(input, update = false) {
    const previous = update ? this.get('locations', input.id) : null;
    const oldPoint = previous ? { x: previous.x, y: previous.y } : null;
    const data = { ...previous, ...input };
    if (previous) this.checkVersion(previous, input.version);
    coordinates(this.state.map, data);
    const fields = { code: textValue(data.code, '库位编码'), zone: textValue(data.zone, '区域'), x: data.x, y: data.y,
      type: enumValue(data.type, locationTypes, '库位类型'), capacity: integer(data.capacity, '容量', 1, 1000000000) };
    this.unique('locations', fields.code, previous?.id);
    requireValue(!this.state.locations.some(location => cell(location) === cell(fields) && location.id !== previous?.id), 'occupied_location', '此坐标已有库位');
    const impacted = previous ? this.state.tasks.some(task => !terminal.includes(task.status) && [task.pickup_location_id, task.drop_location_id].includes(previous.id)) : false;
    requireValue(!impacted, 'location_in_use', '库位有未结束任务，不能编辑');
    const openOrderUsesDrop = previous && this.state.orders.some(order => !terminal.includes(order.status) && order.drop_location_id === previous.id);
    requireValue(!openOrderUsesDrop || fields.type === 'drop', 'location_in_use', '未结束订单引用的放货库位必须保持 drop 类型');
    const chargingTarget = previous && this.state.agvs.some(agv => agv.charging_target === previous.id);
    requireValue(!chargingTarget || fields.type === 'charger', 'location_in_use', '车辆正在使用的充电目标必须保持 charger 类型');
    requireValue(!this.state.agvs.some(agv => cell(agv) === cell(fields)) && !this.state.agvs.some(agv => cell(agv.home) === cell(fields)), 'occupied_cell', '车辆位置或回岗位不能编辑');
    if (previous) {
      const quantity = this.state.inventory.filter(stock => stock.location_id === previous.id).reduce((sum, stock) => sum + stock.qty, 0);
      requireValue(fields.capacity >= quantity, 'capacity_exceeded', '容量不能小于当前库存');
      Object.assign(previous, fields); this.touch(previous);
    } else this.state.locations.push(this.entity({ ...fields, map_version: this.state.map.version }));
    this.newMap(map => {
      if (oldPoint && cell(oldPoint) !== cell(fields)) Object.assign(map.nodes.find(node => cell(node) === cell(oldPoint)), { type: 'floor', walkable: true, capacity: 1 });
      Object.assign(map.nodes.find(node => cell(node) === cell(fields)), { type: fields.type, capacity: fields.capacity, walkable: !['shelf', 'blocked'].includes(fields.type) });
    });
    return previous ?? this.state.locations.at(-1);
  }
  stock(input) {
    const sku = this.get('skus', input.sku_id);
    const location = this.get('locations', input.location_id);
    requireValue(['pick', 'shelf', 'floor'].includes(location.type), 'invalid_stock_location', '库存只能存放在拣选点、货架或地面库位');
    const qty = integer(input.qty, '库存数量', 0, 1000000000);
    const total = this.state.inventory.filter(stock => stock.location_id === location.id).reduce((sum, stock) => sum + stock.qty, 0);
    requireValue(total + qty <= location.capacity, 'capacity_exceeded', '库位容量不足');
    const stock = this.entity({ sku_id: sku.id, location_id: location.id, qty, reserved_qty: 0, batch: String(input.batch ?? '').slice(0, 120), expire_at: isoDate(input.expire_at) });
    this.state.inventory.push(stock);
    this.ledger(stock, null, 'receive', qty, 0);
    return stock;
  }
  ledger(stock, orderId, type, qtyDelta, reservedDelta) {
    this.state.ledger.push(this.entity({ inventory_id: stock.id, order_id: orderId, tick: this.state.tick, type, qty_delta: qtyDelta, reserved_delta: reservedDelta }));
    this.event('inventory.changed', stock.id, { order_id: orderId, type, qty_delta: qtyDelta, reserved_delta: reservedDelta });
  }
  order(input) {
    const code = textValue(input.code, '订单单号');
    this.unique('orders', code);
    requireValue(Array.isArray(input.lines) && input.lines.length > 0 && input.lines.length <= 100, 'invalid_lines', '订单需要 1～100 条明细');
    const lines = input.lines.map(line => {
      requireValue(line && typeof line === 'object' && !Array.isArray(line), 'invalid_line', '每条订单明细必须为包含 sku_id 和 qty 的对象');
      this.get('skus', line.sku_id);
      return { id: this.id(), sku_id: line.sku_id, qty: integer(line.qty, '出库数量', 1, 1000000) };
    });
    const drop = input.drop_location_id ? this.get('locations', input.drop_location_id) : this.state.locations.find(location => location.type === 'drop');
    requireValue(drop?.type === 'drop', 'invalid_drop', '必须指定放货点');
    const order = this.entity({ code, type: 'outbound', status: 'draft', priority: integer(input.priority ?? 5, '优先级', 1, 10),
      requester: textValue(input.requester ?? '演示用户', '创建人'), lines, drop_location_id: drop.id, allocations: [], created_tick: this.state.tick });
    this.state.orders.push(order);
    this.event('order.created', order.id, { code });
    return order;
  }
  orderStatus(order, status) {
    const transitions = { draft: ['allocated', 'cancelled'], allocated: ['released', 'cancelled'], released: ['completed', 'failed'] };
    requireValue(transitions[order.status]?.includes(status), 'invalid_transition', `订单不能从 ${order.status} 变为 ${status}`);
    const previous = order.status;
    order.status = status; this.touch(order);
    this.event(`order.${status}`, order.id, { from: previous, status });
  }
  allocate(id) {
    const order = this.get('orders', id);
    if (['allocated', 'released', 'completed'].includes(order.status)) return order;
    requireValue(order.status === 'draft', 'invalid_transition', '只能预占草稿订单库存');
    const provisional = new Map();
    const allocations = [];
    for (const line of order.lines) {
      const sku = this.get('skus', line.sku_id);
      const stocks = this.state.inventory.filter(stock => stock.sku_id === sku.id && (!stock.expire_at || stock.expire_at > this.now()));
      stocks.sort((left, right) => {
        if (sku.batch_rule === 'fefo') return (left.expire_at ?? '9999').localeCompare(right.expire_at ?? '9999') || left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id);
        return left.created_at.localeCompare(right.created_at) || this.state.inventory.indexOf(left) - this.state.inventory.indexOf(right);
      });
      let remaining = line.qty;
      for (const stock of stocks) {
        const available = stock.qty - stock.reserved_qty - (provisional.get(stock.id) ?? 0);
        const qty = Math.min(available, remaining);
        if (qty > 0) {
          provisional.set(stock.id, (provisional.get(stock.id) ?? 0) + qty);
          allocations.push({ stock_id: stock.id, sku_id: sku.id, location_id: stock.location_id, qty, consumed: false, released: false });
          remaining -= qty;
        }
        if (!remaining) break;
      }
      requireValue(remaining === 0, 'insufficient_stock', `${sku.name} 可用库存不足（缺 ${remaining}）`);
    }
    for (const allocation of allocations) {
      const stock = this.get('inventory', allocation.stock_id);
      stock.reserved_qty += allocation.qty; this.touch(stock);
      this.ledger(stock, order.id, 'reserve', 0, allocation.qty);
    }
    order.allocations = allocations;
    this.orderStatus(order, 'allocated');
    return order;
  }
  pickupPoints(location) {
    const index = mapIndex(this.state.map);
    if (index.get(cell(location))?.walkable) return [{ x: location.x, y: location.y }];
    if (location.type !== 'shelf') return [];
    const points = [{ x: location.x - 1, y: location.y }, { x: location.x + 1, y: location.y }, { x: location.x, y: location.y - 1 }, { x: location.x, y: location.y + 1 }];
    return points.filter(point => index.get(cell(point))?.walkable);
  }
  release(id) {
    const order = this.get('orders', id);
    if (['released', 'completed'].includes(order.status)) return order;
    if (order.status === 'draft') this.allocate(id);
    requireValue(order.status === 'allocated', 'invalid_transition', '订单状态不允许释放');
    const drop = this.get('locations', order.drop_location_id);
    requireValue(drop.type === 'drop' && mapIndex(this.state.map).get(cell(drop))?.walkable, 'invalid_drop', '订单放货点必须为可通行 drop 库位');
    for (let index = 0; index < order.allocations.length; index++) {
      const allocation = order.allocations[index];
      const pickup = this.get('locations', allocation.location_id);
      requireValue(this.pickupPoints(pickup).length, 'inaccessible_pickup', `库位 ${pickup.code} 没有可用取货点`);
      const task = this.entity({ order_id: order.id, type: 'pick_and_drop', priority: order.priority, status: 'pending',
        pickup_location_id: pickup.id, drop_location_id: order.drop_location_id, assigned_agv_id: null,
        plan_version: 0, retry_count: 0, created_tick: this.state.tick, allocation_index: index, sku_id: allocation.sku_id, qty: allocation.qty });
      this.state.tasks.push(task);
      this.event('task.created', task.id, { order_id: order.id });
    }
    this.orderStatus(order, 'released');
    return order;
  }
  releaseAllocation(order, allocation) {
    if (allocation.consumed || allocation.released) return;
    const stock = this.get('inventory', allocation.stock_id);
    stock.reserved_qty -= allocation.qty; allocation.released = true; this.touch(stock);
    this.ledger(stock, order.id, 'release', 0, -allocation.qty);
  }
  cancelOrder(id) {
    const order = this.get('orders', id);
    requireValue(['draft', 'allocated'].includes(order.status), 'invalid_transition', '只有草稿或已分配订单允许取消；已释放订单应取消可取消任务并记为执行失败');
    for (const allocation of order.allocations) this.releaseAllocation(order, allocation);
    this.orderStatus(order, 'cancelled');
    return order;
  }
  taskStatus(task, status, source = 'user') {
    const transitions = { pending: ['assigned', 'cancelled'], assigned: ['pending', 'running', 'cancelled'], running: ['completed', 'failed'] };
    requireValue(transitions[task.status]?.includes(status), 'invalid_transition', `任务不能从 ${task.status} 变为 ${status}`);
    requireValue(!['running', 'completed'].includes(status) || source === 'simulation', 'simulation_only', '运行和完成状态只能由仿真确认');
    const previous = task.status;
    task.status = status; this.touch(task);
    this.event(`task.${status}`, task.id, { from: previous, status, agv_id: task.assigned_agv_id, plan_version: task.plan_version, order_id: task.order_id });
  }
  clearVehicle(agv) {
    this.reservations?.release(agv.id);
    agv.path = []; agv.active_task_id = null; agv.payload_sku = null; agv.charging_target = null;
    agv.recovery_pending = null; agv.replan_after_retreat = false;
    if (!['fault', 'offline'].includes(agv.status)) agv.status = 'idle';
    this.touch(agv);
  }
  failOrder(id, reason, cancelledTaskId = null) {
    const order = this.get('orders', id);
    if (order.status !== 'released') return;
    // cancelTask may already have stopped a vehicle before entering this method.
    let stoppedVehicle = cancelledTaskId !== null;
    for (const task of this.state.tasks.filter(task => task.order_id === id && !terminal.includes(task.status))) {
      this.taskStatus(task, task.status === 'running' ? 'failed' : 'cancelled', 'simulation');
      task.failure_reason = reason;
      const agv = this.state.agvs.find(agv => agv.active_task_id === task.id);
      if (agv) { this.clearVehicle(agv); stoppedVehicle = true; }
    }
    for (const allocation of order.allocations) this.releaseAllocation(order, allocation);
    this.orderStatus(order, 'failed');
    this.event('order.failure.reason', order.id, { reason, cancelled_task_id: cancelledTaskId });
    if (stoppedVehicle) this.replanAll();
  }
  cancelTask(id) {
    const task = this.get('tasks', id);
    requireValue(['pending', 'assigned'].includes(task.status), 'invalid_transition', '只有待处理或已分配任务可以取消');
    this.taskStatus(task, 'cancelled');
    const agv = this.state.agvs.find(agv => agv.active_task_id === task.id);
    if (agv) this.clearVehicle(agv);
    const order = this.get('orders', task.order_id);
    this.releaseAllocation(order, order.allocations[task.allocation_index]);
    this.failOrder(order.id, '拣选任务被取消', task.id);
    return task;
  }
  priority(id, priority) {
    const task = this.get('tasks', id);
    requireValue(!terminal.includes(task.status), 'terminal_task', '终态任务不能调整优先级');
    task.priority = integer(priority, '优先级', 1, 10); this.touch(task);
    this.event('task.priority.changed', task.id, { priority });
    return task;
  }
  rebuildReservations() {
    this.reservations = new ReservationTable(this.state.map);
    const horizon = this.state.tick + 240;
    for (const agv of this.state.agvs) {
      const current = { x: agv.x, y: agv.y, tick: this.state.tick, action: 'wait' };
      const points = [current, ...agv.path.filter(point => point.tick > this.state.tick)];
      const result = this.reservations.reserve(agv.id, agv.plan_version, points, Math.max(horizon, points.at(-1).tick));
      requireValue(result.ok, 'unsafe_snapshot', '运行态路径预约存在冲突', result);
    }
  }
  route(agv, goals, task = null) {
    const points = [{ x: agv.x, y: agv.y, tick: this.state.tick, action: 'wait' }];
    let blockers = [];
    for (const [goalIndex, goal] of goals.entries()) {
      const last = points.at(-1);
      const remainingHorizon = Math.min(110, this.state.tick + 200 - last.tick);
      if (remainingHorizon <= 0) return { ok: false, reason: 'planning_horizon_exceeded', blockers: [...new Set(blockers)] };
      const holdTicks = goal.dwell ?? (goal.action === 'unloading' ? 2 : goal.action ? 1 : 0);
      const result = findPath(this.state.map, this.reservations, agv.id, last, goal, last.tick, { horizon: remainingHorizon, holdTicks: goalIndex === goals.length - 1 ? 240 : holdTicks });
      blockers.push(...result.blockers);
      if (!result.ok) return { ...result, blockers: [...new Set(blockers)] };
      points.push(...result.ticks.slice(1));
      if (goal.action) points.push({ x: goal.x, y: goal.y, tick: points.at(-1).tick + 1, action: goal.action });
      if (goal.action === 'unloading') points.push({ x: goal.x, y: goal.y, tick: points.at(-1).tick + 1, action: 'complete' });
      for (let dwell = goal.action ? 1 : 0; dwell < (goal.dwell ?? 0); dwell++) points.push({ x: goal.x, y: goal.y, tick: points.at(-1).tick + 1, action: 'wait' });
    }
    const planVersion = (task?.plan_version ?? agv.plan_version) + 1;
    const result = this.reservations.reserve(agv.id, planVersion, points, Math.max(this.state.tick + 240, points.at(-1).tick));
    if (!result.ok) return { ...result, blockers: [result.blocker] };
    agv.plan_version = planVersion;
    agv.path = points.slice(1);
    agv.map_version = this.state.map.version;
    if (task) {
      task.plan_version = planVersion; this.touch(task);
      task.plan = { taskId: task.id, agvId: agv.id, mapVersion: this.state.map.version, planVersion,
        ticks: points, edges: points.slice(1).map((point, index) => ({ tick: points[index].tick, from: cell(points[index]), to: cell(point) })),
        estimatedTicks: points.length - 1, waitTicks: points.filter(point => point.action === 'wait').length };
    }
    return { ok: true, ticks: points };
  }
  routeTask(agv, task, pickups) {
    const drop = this.get('locations', task.drop_location_id);
    const remaining = [{ ...drop, action: 'unloading' }, agv.home];
    if (agv.payload_sku) return this.route(agv, remaining, task);
    pickups ??= this.pickupPoints(this.get('locations', task.pickup_location_id));
    const blockers = new Set();
    let reason = 'inaccessible_pickup';
    // A walkable shelf face can still be isolated or reserved. Try all faces in stable order.
    for (const point of pickups) {
      const result = this.route(agv, [{ ...point, action: 'loading' }, ...remaining], task);
      if (result.ok) return result;
      reason = result.reason;
      for (const id of result.blockers) blockers.add(id);
    }
    return { ok: false, reason, blockers: [...blockers] };
  }
  dispatch() {
    this.state.waitGraph = {};
    const tasks = this.state.tasks.filter(task => task.status === 'pending').sort((left, right) => right.priority - left.priority || left.created_tick - right.created_tick || this.state.tasks.indexOf(left) - this.state.tasks.indexOf(right));
    for (const task of tasks) {
      if (task.status !== 'pending') continue;
      const dispatchStarted = performance.now();
      const pickups = this.pickupPoints(this.get('locations', task.pickup_location_id));
      const expired = this.state.tick - task.created_tick > this.config.max_wait_ticks * this.config.max_retries;
      if (!pickups.length) {
        if (task.blocked_reason !== 'inaccessible_pickup') {
          task.blocked_reason = 'inaccessible_pickup'; this.touch(task);
          this.event('task.blocked', task.id, { reason: task.blocked_reason, order_id: task.order_id });
        }
        if (expired) this.failOrder(task.order_id, '取货点不可达，等待超时');
        continue;
      }
      if (task.blocked_reason) { delete task.blocked_reason; this.touch(task); }
      const candidates = this.state.agvs.filter(agv => agv.status === 'idle' && !agv.active_task_id && !agv.path.length && agv.battery >= this.config.battery_threshold && !agv.payload_sku);
      const score = agv => Math.min(...pickups.map(point => distance(agv, point))) * (1 + task.priority / 10) + agv.completed_count * 0.2 + (100 - agv.battery) * 0.08;
      candidates.sort((left, right) => score(left) - score(right) || left.code.localeCompare(right.code));
      let assigned = false;
      for (const agv of candidates) {
        const result = this.routeTask(agv, task, pickups);
        if (!result.ok) {
          this.state.waitGraph[agv.id] = result.blockers;
          this.state.metrics.conflicts++;
          this.event('conflict.reservation', agv.id, { reason: result.reason, blockers: result.blockers, task_id: task.id });
          continue;
        }
        task.assigned_agv_id = agv.id;
        agv.active_task_id = task.id; agv.status = 'to_pickup'; agv.wait_ticks = 0; this.touch(agv);
        this.event('agv.status.changed', agv.id, { status: agv.status, task_id: task.id });
        this.taskStatus(task, 'assigned'); assigned = true;
        this.state.metrics.dispatchMs.push(performance.now() - dispatchStarted);
        if (this.state.metrics.dispatchMs.length > 10000) this.state.metrics.dispatchMs.shift();
        break;
      }
      if (!assigned && expired) this.failOrder(task.order_id, '路径或车辆不可用，等待超时');
    }
    this.chargeQueue();
  }
  chargeQueue() {
    const chargers = this.state.locations.filter(location => location.type === 'charger');
    for (const agv of this.state.agvs.filter(agv => agv.status === 'charging' && agv.charging_target && !agv.path.length)) {
      const charger = this.get('locations', agv.charging_target);
      if (cell(agv) !== cell(charger)) this.route(agv, [{ ...charger, action: 'charge' }]);
    }
    const occupied = new Set(this.state.agvs.filter(agv => agv.charging_target).map(agv => agv.charging_target));
    for (const agv of this.state.agvs.filter(agv => agv.status === 'idle' && !agv.active_task_id && !agv.path.length && agv.battery < this.config.battery_threshold)) {
      const candidates = chargers.filter(charger => !occupied.has(charger.id)).sort((left, right) => distance(agv, left) - distance(agv, right) || left.code.localeCompare(right.code));
      for (const charger of candidates) {
        const result = this.route(agv, [{ ...charger, action: 'charge' }]);
        if (result.ok) {
          agv.charging_target = charger.id; agv.status = 'charging'; occupied.add(charger.id); this.touch(agv);
          this.event('agv.charge.queued', agv.id, { location_id: charger.id }); break;
        }
      }
    }
  }
  replanMapChanges(previous) {
    const oldNodes = mapIndex(previous);
    const changed = new Set(this.state.map.nodes.filter(node => {
      const old = oldNodes.get(cell(node));
      return !old || old.walkable !== node.walkable || old.type !== node.type || old.capacity !== node.capacity;
    }).map(cell));
    const affected = new Set(this.state.agvs.filter(agv =>
      [agv, ...agv.path].some(point => changed.has(cell(point))) ||
      (agv.active_task_id && !agv.path.length)).map(agv => agv.id));

    // Clearing a route makes its vehicle stationary. Include only routes that would
    // enter these occupied cells, until the remaining reservations can be kept safely.
    let expanded = true;
    while (expanded) {
      expanded = false;
      const stoppedCells = new Set(this.state.agvs.filter(agv => affected.has(agv.id)).map(cell));
      for (const agv of this.state.agvs) {
        if (!affected.has(agv.id) && agv.path.some(point => stoppedCells.has(cell(point)))) {
          affected.add(agv.id); expanded = true;
        }
      }
    }
    this.replanVehicles(this.state.agvs.filter(agv => affected.has(agv.id)));
    this.event('map.routes.revalidated', this.state.map.id, { map_version: this.state.map.version,
      changed_cells: [...changed], replanned_agv_ids: [...affected],
      preserved_agv_ids: this.state.agvs.filter(agv => !affected.has(agv.id)).map(agv => agv.id) });
  }
  replanAll() { this.replanVehicles(this.state.agvs); }
  replanVehicles(selected) {
    const vehicles = [...selected].sort((left, right) => {
      const priority = agv => this.state.tasks.find(task => task.id === agv.active_task_id)?.priority ?? 0;
      return priority(right) - priority(left) || left.code.localeCompare(right.code);
    });
    for (const agv of vehicles) { agv.path = []; delete this.state.waitGraph[agv.id]; }
    this.rebuildReservations();
    for (const agv of vehicles) {
      const task = this.state.tasks.find(task => task.id === agv.active_task_id);
      if (['fault', 'offline'].includes(agv.status)) continue;
      if (task && !terminal.includes(task.status)) {
        task.plan_version++; this.touch(task);
        agv.plan_version = task.plan_version;
        task.plan = null; // The old route has already been withdrawn from reservations.
        const result = this.routeTask(agv, task);
        this.event('task.replanned', task.id, { plan_version: task.plan_version, ok: result.ok, map_version: this.state.map.version });
        if (!result.ok) {
          this.state.waitGraph[agv.id] = result.blockers;
          this.event('agv.blocked', agv.id, { reason: result.reason });
        }
      } else if (agv.charging_target) {
        const charger = this.get('locations', agv.charging_target);
        agv.status = 'charging';
        if (cell(agv) !== cell(charger)) this.route(agv, [{ ...charger, action: 'charge' }]);
      } else if (cell(agv) !== cell(agv.home)) this.route(agv, [agv.home]);
    }
  }
  block(input, blocked) {
    coordinates(this.state.map, input);
    const node = mapIndex(this.state.map).get(cell(input));
    requireValue(!this.state.agvs.some(agv => cell(agv) === cell(input)) && !this.state.agvs.some(agv => cell(agv.home) === cell(input)), 'occupied_cell', '不能封闭车辆所在格或回岗位');
    if (blocked) requireValue(node.type === 'floor', 'invalid_block', '只能临时封闭地面通行格');
    else requireValue(node.type === 'blocked', 'invalid_unblock', '该格未被封闭');
    this.newMap(map => Object.assign(map.nodes.find(node => cell(node) === cell(input)), { type: blocked ? 'blocked' : 'floor', walkable: !blocked }));
    this.event(blocked ? 'map.blocked' : 'map.unblocked', this.state.map.id, { x: input.x, y: input.y, map_version: this.state.map.version });
    return this.state.map.version;
  }
  fault(id, offline = false) {
    let agv = this.get('agvs', id);
    const task = this.state.tasks.find(task => task.id === agv.active_task_id);
    agv.status = offline ? 'offline' : 'fault'; agv.fault_code = offline ? 'manual_offline' : 'injected_fault';
    if (task?.status === 'assigned') {
      this.taskStatus(task, 'pending'); task.assigned_agv_id = null; task.retry_count++; task.plan_version++; this.touch(task);
      this.event('task.reassigned', task.id, { reason: 'agv_fault', previous_agv_id: agv.id });
    } else if (task?.status === 'running') {
      this.failOrder(task.order_id, '执行中 AGV 故障，旧任务失败后创建替代任务');
      const previousOrder = this.get('orders', task.order_id);
      const remaining = this.state.tasks.filter(item => item.order_id === previousOrder.id && item.status !== 'completed').map(item => ({ sku_id: item.sku_id, qty: item.qty }));
      if (remaining.length && task.retry_count < this.config.max_retries) {
        const retryBefore = clone(this.state);
        const pendingCount = this.pendingEvents.length;
        try {
          const replacement = this.order({ code: `${previousOrder.code}-R${this.state.sequence}`, requester: previousOrder.requester, priority: previousOrder.priority, lines: remaining, drop_location_id: previousOrder.drop_location_id });
          this.release(replacement.id);
          for (const item of this.state.tasks.filter(item => item.order_id === replacement.id)) item.retry_count = task.retry_count + 1;
          this.event('task.reassigned', task.id, { replacement_order_id: replacement.id });
        } catch (error) {
          this.state = retryBefore; this.pendingEvents.length = pendingCount;
          agv = this.get('agvs', id); this.rebuildReservations();
          this.event('task.reassignment.failed', task.id, { reason: error.message, code: error.code });
        }
      }
    }
    this.clearVehicle(agv);
    this.event(offline ? 'agv.offline' : 'agv.fault', agv.id, { task_id: task?.id ?? null });
    this.replanAll();
    return agv;
  }
  recover(id) {
    const agv = this.get('agvs', id);
    requireValue(['fault', 'offline'].includes(agv.status), 'invalid_transition', '只有故障或离线车辆可恢复');
    agv.status = 'idle'; agv.fault_code = null; agv.retreat_count = 0; this.touch(agv);
    this.event('agv.recovered', agv.id);
    this.replanAll(); return agv;
  }
  recoverDeadlock(graph = this.state.waitGraph) {
    const cycles = cyclesOf(graph);
    if (!cycles.length) return false;
    const cycle = cycles[0];
    this.state.metrics.deadlocks++;
    this.event('deadlock.detected', this.state.map.id, { cycle });
    const candidates = cycle.map(id => this.get('agvs', id)).filter(agv => !['fault', 'offline'].includes(agv.status) && agv.retreat_count < this.config.max_retries);
    candidates.sort((left, right) => (this.state.tasks.find(task => task.id === left.active_task_id)?.priority ?? 0) - (this.state.tasks.find(task => task.id === right.active_task_id)?.priority ?? 0) || left.code.localeCompare(right.code));
    for (const agv of candidates) {
      const originalPath = agv.path;
      this.reservations.release(agv.id);
      const escapeCells = neighbors(this.state.map, agv, false).filter(point => !this.state.agvs.some(other => other.id !== agv.id && cell(other) === cell(point)));
      for (const point of escapeCells) {
        const activeTask = this.state.tasks.find(task => task.id === agv.active_task_id);
        const result = this.route(agv, [point], activeTask);
        if (!result.ok) continue;
        agv.retreat_count++; agv.wait_ticks = 0;
        this.event('deadlock.retreat', agv.id, { target: point, retreat_count: agv.retreat_count, released_reservations: true });
        agv.recovery_pending = { cycle, target: point, task_id: activeTask?.id ?? null, retreat_plan_version: agv.plan_version };
        agv.replan_after_retreat = Boolean(agv.active_task_id);
        this.state.waitGraph = {};
        return true;
      }
      agv.path = originalPath;
      this.rebuildReservations();
    }
    for (const agv of candidates) if (agv.active_task_id) this.failOrder(this.get('tasks', agv.active_task_id).order_id, '死锁回退失败，达到有界恢复策略');
    return false;
  }
  complete(task, agv) {
    const order = this.get('orders', task.order_id);
    const allocation = order.allocations[task.allocation_index];
    requireValue(!allocation.consumed && !allocation.released, 'invalid_allocation', '预占记录已结算');
    const stock = this.get('inventory', allocation.stock_id);
    stock.qty -= allocation.qty; stock.reserved_qty -= allocation.qty; allocation.consumed = true; this.touch(stock);
    this.ledger(stock, order.id, 'ship', -allocation.qty, -allocation.qty);
    task.completed_tick = this.state.tick;
    this.taskStatus(task, 'completed', 'simulation');
    agv.completed_count++; agv.active_task_id = null; agv.payload_sku = null; agv.status = 'idle';
    if (this.state.tasks.filter(item => item.order_id === order.id).every(item => item.status === 'completed')) this.orderStatus(order, 'completed');
  }
  step() {
    const before = clone(this.state);
    try {
      this.dispatch();
      const nextTick = this.state.tick + 1;
      const moves = this.state.agvs.map(agv => ({ agv, from: { x: agv.x, y: agv.y }, next: agv.path.find(point => point.tick === nextTick) ?? { x: agv.x, y: agv.y, tick: nextTick, action: 'wait' } }));
      for (let index = 0; index < moves.length; index++) {
        for (let other = index + 1; other < moves.length; other++) {
          requireValue(cell(moves[index].next) !== cell(moves[other].next), 'vertex_conflict', '安全检查阻止同格运动');
          requireValue(!(cell(moves[index].from) === cell(moves[other].next) && cell(moves[other].from) === cell(moves[index].next)), 'opposite_edge', '安全检查阻止相向穿边');
        }
      }
      this.state.tick = nextTick;
      for (const { agv, from, next } of moves) {
        const previousStatus = agv.status;
        const task = this.state.tasks.find(task => task.id === agv.active_task_id);
        const busy = Boolean(task) || agv.status === 'charging';
        if (busy) agv.busy_ticks++;
        if (task?.status === 'assigned') { task.started_tick = nextTick; this.taskStatus(task, 'running', 'simulation'); }
        if (distance(from, next)) {
          requireValue(mapIndex(this.state.map).get(cell(next))?.walkable, 'blocked_path', '车辆不能驶入封闭区域');
          agv.x = next.x; agv.y = next.y;
          agv.battery = Math.max(0, agv.battery - this.config.move_cost);
          this.state.visits[cell(next)] = (this.state.visits[cell(next)] ?? 0) + 1;
          this.event('agv.position.changed', agv.id, { x: agv.x, y: agv.y, battery: agv.battery, task_id: task?.id ?? null, plan_version: agv.plan_version });
        }
        if (task && next.action === 'loading') {
          agv.status = 'loading'; agv.payload_sku = task.sku_id;
          this.event('agv.loading', agv.id, { task_id: task.id });
        } else if (task && next.action === 'unloading') {
          agv.status = 'unloading'; this.event('agv.unloading', agv.id, { task_id: task.id });
        } else if (task && next.action === 'complete') {
          this.complete(task, agv);
        } else if (task && agv.payload_sku) agv.status = 'to_dropoff';
        else if (task) agv.status = 'to_pickup';
        agv.path = agv.path.filter(point => point.tick > nextTick);
        if (agv.recovery_pending && !agv.path.length && !agv.active_task_id && !['fault', 'offline'].includes(agv.status) && cell(agv) === cell(agv.recovery_pending.target) && agv.plan_version === agv.recovery_pending.retreat_plan_version) {
          this.state.metrics.recoveries++;
          this.event('deadlock.recovered', agv.id, { ...agv.recovery_pending, plan_version: agv.plan_version });
          agv.recovery_pending = null;
        }
        if (agv.status === 'charging' && !agv.path.length && agv.charging_target && cell(agv) === cell(this.get('locations', agv.charging_target))) {
          agv.battery = Math.min(100, agv.battery + this.config.charging_per_tick);
          this.event('agv.charging', agv.id, { battery: agv.battery });
          if (agv.battery >= 100) {
            agv.charging_target = null; agv.status = 'idle';
            this.event('agv.charge.completed', agv.id);
            this.route(agv, [agv.home]);
          }
        }
        if (task && !agv.path.length && next.action !== 'unloading') agv.wait_ticks++;
        else agv.wait_ticks = 0;
        agv.history.push({ tick: nextTick, x: agv.x, y: agv.y });
        if (agv.history.length > 80) agv.history.shift();
        agv.last_tick = nextTick; this.touch(agv);
        if (agv.status !== previousStatus) this.event('agv.status.changed', agv.id, { status: agv.status, task_id: agv.active_task_id, plan_version: agv.plan_version });
      }
      this.rebuildReservations();
      for (const agv of this.state.agvs.filter(agv => agv.active_task_id && !agv.path.length)) {
        if (!agv.active_task_id || agv.path.length) continue;
        if (agv.wait_ticks > this.config.max_wait_ticks) {
          this.failOrder(this.get('tasks', agv.active_task_id).order_id, '路径阻塞超过等待上限');
          continue;
        }
        const task = this.get('tasks', agv.active_task_id);
        const result = this.routeTask(agv, task);
        if (!result.ok) this.state.waitGraph[agv.id] = result.blockers;
        else {
          if (agv.recovery_pending && !['fault', 'offline'].includes(agv.status) && cell(agv) === cell(agv.recovery_pending.target)) {
            this.state.metrics.recoveries++;
            this.event('deadlock.recovered', agv.id, { ...agv.recovery_pending, plan_version: agv.plan_version });
            agv.recovery_pending = null;
          }
          agv.replan_after_retreat = false; this.event('task.replanned', task.id, { plan_version: task.plan_version, ok: true });
        }
      }
      if (nextTick % 5 === 0) this.recoverDeadlock();
      this.assertInvariants();
      this.persist();
      return this.state.tick;
    } catch (error) {
      this.state = before; this.pendingEvents = []; this.rebuildReservations(); throw error;
    }
  }
  assertInvariants() {
    requireValue(new Set(this.state.agvs.map(cell)).size === this.state.agvs.length, 'vertex_conflict', '车辆格点必须唯一');
    for (const stock of this.state.inventory) requireValue(stock.qty >= 0 && stock.reserved_qty >= 0 && stock.reserved_qty <= stock.qty, 'invalid_inventory', '库存/预占不变量损坏');
    const assigned = this.state.tasks.filter(task => !terminal.includes(task.status) && task.assigned_agv_id).map(task => task.assigned_agv_id);
    requireValue(new Set(assigned).size === assigned.length, 'double_assignment', '一台车辆不能同时执行两个任务');
    for (const agv of this.state.agvs) requireValue(Number.isFinite(agv.battery) && agv.battery >= 0 && agv.battery <= 100, 'invalid_battery', '电量必须在 0～100');
    return true;
  }
  query(collection, filters = {}) {
    const items = collection === 'events' ? this.store.events() : this.state[collection];
    requireValue(Array.isArray(items), 'invalid_collection', '不支持此查询类型');
    return clone(this.filterRecords(items, filters));
  }
  eventPage(filters = {}) {
    const offset = integer(Number(filters.offset ?? 0), '分页起点');
    const limit = integer(Number(filters.limit ?? 40), '每页条数', 1, 200);
    const events = this.store.events();
    const rows = this.filterRecords(events, filters).reverse();
    return { items: clone(rows.slice(offset, offset + limit)), total: rows.length, offset, limit,
      types: [...new Set(events.map(event => event.type))].sort(), tick: this.state.tick };
  }
  filterRecords(items, filters) {
    const fromTick = filters.from_tick === undefined ? 0 : integer(Number(filters.from_tick), '起始 tick');
    const toTick = filters.to_tick === undefined ? Number.MAX_SAFE_INTEGER : integer(Number(filters.to_tick), '结束 tick');
    const fromDate = filters.from_date ? isoDate(filters.from_date) : null;
    const toDate = filters.to_date ? isoDate(filters.to_date) : null;
    requireValue(fromTick <= toTick && (!fromDate || !toDate || fromDate <= toDate), 'invalid_window', '查询开始时间不能晚于结束时间');
    const agvId = filters.agv_id ?? filters.agv;
    const contains = (value, id) => value === id || (Array.isArray(value) ? value.some(child => contains(child, id))
      : value && typeof value === 'object' ? Object.values(value).some(child => contains(child, id)) : false);
    const related = (item, id) => item.id === id || item.aggregateId === id || contains(item.payload, id);
    const search = String(filters.search ?? '').trim().toLowerCase();
    return items.filter(item =>
      (!filters.status || item.status === filters.status) && (!filters.priority || item.priority === Number(filters.priority)) &&
      (!filters.code || (item.code ?? '').includes(filters.code)) &&
      (!agvId || item.assigned_agv_id === agvId || related(item, agvId)) &&
      (!filters.task_id || related(item, filters.task_id)) &&
      (!filters.type || item.type === filters.type) &&
      (!search || `${item.type ?? ''} ${item.aggregateId ?? ''} ${JSON.stringify(item.payload ?? {})}`.toLowerCase().includes(search)) &&
      ((item.tick ?? item.created_tick ?? 0) >= fromTick && (item.tick ?? item.created_tick ?? 0) <= toTick) &&
      (!fromDate || (item.occurredAt ?? item.created_at) >= fromDate) && (!toDate || (item.occurredAt ?? item.created_at) <= toDate));
  }
  demoOrders(urgent = false) {
    const orders = [];
    for (let index = 0; index < (urgent ? 1 : 6); index++) {
      const order = this.order({ code: `${urgent ? 'URGENT' : 'DEMO'}-${this.state.tick}-${this.state.sequence}-${index + 1}`, requester: '演示用户', priority: urgent ? 10 : 3 + index % 5,
        lines: [{ sku_id: this.state.skus[index % this.state.skus.length].id, qty: 2 + index % 3 }] });
      this.release(order.id); orders.push(order);
    }
    if (urgent) this.event('order.urgent.injected', orders[0].id, { priority: 10 });
    return orders;
  }
  demoConflict() {
    const target = { x: 7, y: 7, action: 'demo_hold', dwell: 6 };
    requireValue(mapIndex(this.state.map).get(cell(target))?.walkable, 'demo_unavailable', '竞争演示格点被封闭，请先解封');
    const vehicles = this.state.agvs.filter(agv => agv.status === 'idle' && !agv.path.length && !agv.active_task_id).sort((left, right) => distance(left, target) - distance(right, target) || left.code.localeCompare(right.code));
    requireValue(vehicles.length >= 2, 'no_idle_agvs', '竞争演示需要两台已回岗空闲车');
    const [first, second] = vehicles;
    const reserve = this.route(first, [target, first.home]);
    requireValue(reserve.ok, 'demo_unavailable', '首车竞争路线暂不可达');
    const naive = findPath(this.state.map, new ReservationTable(this.state.map), second.id, second, target, this.state.tick);
    requireValue(naive.ok, 'demo_unavailable', '第二台车无法到达演示格点');
    const proposed = [...naive.ticks];
    for (let dwell = 0; dwell < target.dwell; dwell++) proposed.push({ ...target, tick: proposed.at(-1).tick + 1 });
    let conflict = null;
    for (let index = 0; index < proposed.length; index++) conflict ??= this.reservations.conflict(second.id, proposed[index - 1], proposed[index]);
    requireValue(conflict, 'demo_unavailable', '当前车位没有形成竞争，请等待车辆回到初始岗位后重试');
    const reroute = this.route(second, [target, second.home]);
    requireValue(reroute.ok, 'demo_unavailable', '第二台车竞争后暂无安全绕行路线');
    this.state.metrics.conflicts++;
    this.event('conflict.demonstrated', first.id, { ...conflict, agv_id: second.id, vehicles: [first.id, second.id], resolution: 'wait_or_detour', route: reroute.ticks });
    return { ok: true, conflict, vehicles: [first.id, second.id], firstRoute: first.path, reroute };
  }
  demoDeadlock() {
    const candidates = this.state.agvs.filter(agv => agv.status === 'idle' && !agv.path.length && !agv.active_task_id).slice(0, 3);
    requireValue(candidates.length === 3, 'no_idle_agvs', '演示需要三台空闲且已回岗车辆');
    const graph = Object.fromEntries(candidates.map((agv, index) => [agv.id, [candidates[(index + 1) % candidates.length].id]]));
    this.state.waitGraph = graph;
    this.event('deadlock.scenario.injected', this.state.map.id, { graph, source: 'explicit_wait_graph_fixture' });
    return { graph, recovered: this.recoverDeadlock(graph) };
  }
  command(input) {
    const before = clone(this.state);
    try {
      let result;
      switch (input.action) {
        case 'sku.create': result = this.sku(input); break;
        case 'sku.update': result = this.sku(input, true); break;
        case 'sku.delete': result = this.deleteSku(input.id); break;
        case 'location.create': result = this.location(input); break;
        case 'location.update': result = this.location(input, true); break;
        case 'inventory.add': result = this.stock(input); break;
        case 'order.create': result = this.order(input); break;
        case 'order.allocate': result = this.allocate(input.id); break;
        case 'order.release': result = this.release(input.id); break;
        case 'order.cancel': result = this.cancelOrder(input.id); break;
        case 'task.cancel': result = this.cancelTask(input.id); break;
        case 'task.priority': result = this.priority(input.id, input.priority); break;
        case 'task.transition': throw new DomainError('simulation_only', '任务状态由调度与仿真驱动，禁止直接篡改');
        case 'sim.start': this.state.running = true; result = true; break;
        case 'sim.pause': this.state.running = false; result = true; break;
        case 'sim.speed': this.state.speed = enumValue(input.speed, [1, 4, 8], '速度'); result = this.state.speed; break;
        case 'sim.step': requireValue(!this.state.running, 'pause_required', '请先暂停再单步'); result = this.step(); break;
        case 'sim.reset':
          integer(input.seed ?? this.config.seed, 'seed', 0, 4294967295);
          this.store.reset(); this.initialize(input.seed ?? this.config.seed); return { seed: this.state.seed };
        case 'inject.block': result = this.block(input, true); break;
        case 'inject.unblock': result = this.block(input, false); break;
        case 'inject.fault': result = this.fault(input.agv_id); break;
        case 'inject.offline': result = this.fault(input.agv_id, true); break;
        case 'inject.recover': result = this.recover(input.agv_id); break;
        case 'inject.urgent': result = this.demoOrders(true); break;
        case 'demo.orders': result = this.demoOrders(); break;
        case 'demo.conflict': result = this.demoConflict(); break;
        case 'demo.deadlock': result = this.demoDeadlock(); break;
        default: throw new DomainError('unknown_action', '未知操作');
      }
      this.assertInvariants(); this.persist(input.action !== 'sim.step');
      return clone(result);
    } catch (error) { this.state = before; this.pendingEvents = []; this.rebuildReservations(); throw error; }
  }
  close() { this.persist(false); this.store.close(); }
}
