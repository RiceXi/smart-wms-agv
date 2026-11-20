import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { Engine } from '../src/engine.js';
import { ReservationTable, findPath } from '../src/planner.js';
import { validatePath, rebuildEdges } from '../src/map-engine.js';
import { cell, distance, cyclesOf } from '../src/common.js';
import { createApplication } from '../src/server.js';

function setup(context, options = {}) {
  const engine = new Engine({ recording: false, ...options });
  context.after(() => engine.close());
  return engine;
}
const act = (engine, action, fields = {}) => engine.command({ ...fields, action });
function createOrder(engine, fields = {}) {
  return act(engine, 'order.create', { code: `TEST-${engine.state.sequence}`, priority: 5, requester: '验收', lines: [{ sku_id: engine.state.skus[0].id, qty: 3 }], ...fields });
}
function advance(engine, count) { for (let tick = 0; tick < count; tick++) engine.step(); }
function checkedMoves(engine, count) {
  for (let tick = 0; tick < count; tick++) {
    const before = engine.state.agvs.map(agv => ({ id: agv.id, x: agv.x, y: agv.y }));
    engine.step();
    const after = engine.state.agvs;
    assert.equal(new Set(after.map(cell)).size, after.length);
    for (let index = 0; index < before.length; index++) {
      assert.ok(distance(before[index], after[index]) <= 1);
      for (let other = index + 1; other < before.length; other++) assert.ok(!(cell(before[index]) === cell(after[other]) && cell(before[other]) === cell(after[index])));
    }
  }
}

test('AC1 HTTP 启动、健康检查、JSON-RPC 和断线重连完整快照', async context => {
  const engine = new Engine({ recording: false });
  const app = createApplication({ engine });
  await new Promise(resolveReady => app.server.listen(0, '127.0.0.1', resolveReady));
  context.after(() => app.close());
  const base = `http://127.0.0.1:${app.server.address().port}`;
  assert.equal((await fetch(`${base}/health`).then(response => response.json())).status, 'ok');
  const rpc = await fetch(`${base}/rpc`, { method: 'POST', body: JSON.stringify({ jsonrpc: '2.0', id: 7, method: 'snapshot' }) }).then(response => response.json());
  assert.equal(rpc.id, 7); assert.equal(rpc.result.agvs.length, 10);
  const created = await fetch(`${base}/api/skus`, { method: 'POST', body: JSON.stringify({ code: 'REST-TEST', name: 'HTTP商品', batch_rule: 'none' }) });
  assert.equal(created.status, 201);
  const sku = await created.json();
  assert.equal((await fetch(`${base}/api/skus/${sku.id}`).then(response => response.json())).code, 'REST-TEST');
  assert.equal((await fetch(`${base}/api/skus/${sku.id}`, { method: 'DELETE' })).status, 200);
  for (let attempt = 0; attempt < 2; attempt++) {
    const controller = new AbortController();
    const response = await fetch(`${base}/api/stream`, { signal: controller.signal });
    const reader = response.body.getReader();
    let body = '';
    while (!body.includes('\n\n')) body += new TextDecoder().decode((await reader.read()).value);
    const snapshot = JSON.parse(body.split('data: ')[1].split('\n\n')[0]);
    assert.equal(snapshot.tick, engine.state.tick); assert.equal(snapshot.map.nodes.length, 352); assert.equal(snapshot.agvs.length, 10);
    controller.abort(); await reader.cancel().catch(() => {});
    engine.step();
  }
  const malformed = await fetch(`${base}/api/command`, { method: 'POST', body: '{broken' });
  assert.equal(malformed.status, 400);
  const foreign = await fetch(`${base}/api/command`, { method: 'POST', headers: { origin: 'https://foreign.invalid' }, body: '{}' });
  assert.equal(foreign.status, 403);
});

test('AC2 新增3个SKU和5库位，拒绝重复编码/越界/负库存，支持编辑删除', context => {
  const engine = setup(context);
  for (let index = 0; index < 3; index++) act(engine, 'sku.create', { code: `NEW-${index}`, name: `商品${index}`, batch_rule: 'fifo' });
  for (let index = 0; index < 5; index++) {
    const location = act(engine, 'location.create', { code: `L-${index}`, zone: 'A', x: 18 + index % 3, y: 12 + Math.floor(index / 3), type: 'pick', capacity: 100 });
    act(engine, 'inventory.add', { sku_id: engine.state.skus[3].id, location_id: location.id, qty: 10, batch: 'B1' });
  }
  assert.throws(() => act(engine, 'sku.create', { code: 'NEW-0', name: '重复' }), /已存在/);
  assert.throws(() => act(engine, 'location.create', { code: 'BAD', zone: 'A', x: -1, y: 1, type: 'pick', capacity: 1 }), /横坐标/);
  assert.throws(() => act(engine, 'inventory.add', { sku_id: engine.state.skus[0].id, location_id: engine.state.locations[0].id, qty: -1 }), /库存数量/);
  const sku = act(engine, 'sku.create', { code: 'DELETE', name: '可删商品' });
  act(engine, 'sku.update', { id: sku.id, name: '改名', version: sku.version });
  assert.throws(() => act(engine, 'sku.update', { id: sku.id, name: '旧写入', version: sku.version }), /已更新/);
  act(engine, 'sku.delete', { id: sku.id });
  assert.throws(() => act(engine, 'sku.delete', { id: engine.state.skus[0].id }), /引用/);
});

test('AC3 多行订单、原子预占、幂等释放、取消释放及FIFO/FEFO', context => {
  const engine = setup(context);
  const order = createOrder(engine, { lines: engine.state.skus.map(sku => ({ sku_id: sku.id, qty: 2 })) });
  act(engine, 'order.release', { id: order.id });
  const stock = structuredClone(engine.state.inventory);
  const tasks = engine.state.tasks.length;
  act(engine, 'order.release', { id: order.id });
  assert.deepEqual(engine.state.inventory, stock); assert.equal(engine.state.tasks.length, tasks);
  assert.equal(engine.get('orders', order.id).status, 'released');
  const bad = createOrder(engine, { lines: [{ sku_id: engine.state.skus[0].id, qty: 2 }, { sku_id: engine.state.skus[1].id, qty: 999999 }] });
  assert.throws(() => act(engine, 'order.release', { id: bad.id }), /库存不足/);
  assert.deepEqual(engine.state.inventory, stock);
  const cancel = createOrder(engine);
  act(engine, 'order.allocate', { id: cancel.id });
  act(engine, 'order.cancel', { id: cancel.id });
  assert.deepEqual(engine.state.inventory.map(item => item.reserved_qty), stock.map(item => item.reserved_qty));
  assert.throws(() => act(engine, 'order.release', { id: cancel.id }), /不允许/);
  const fefo = engine.state.skus[2];
  const early = act(engine, 'inventory.add', { sku_id: fefo.id, location_id: engine.state.locations[2].id, qty: 4, expire_at: '2027-01-01', batch: 'EARLY' });
  const selected = createOrder(engine, { lines: [{ sku_id: fefo.id, qty: 3 }] });
  act(engine, 'order.allocate', { id: selected.id });
  assert.equal(engine.get('orders', selected.id).allocations[0].stock_id, early.id);
  const fifo = createOrder(engine);
  act(engine, 'order.allocate', { id: fifo.id });
  assert.equal(engine.get('orders', fifo.id).allocations[0].stock_id, engine.state.inventory[0].id);
});

test('AC4 任务状态只能由调度/仿真推进，终态拒绝重启', context => {
  const engine = setup(context);
  const order = createOrder(engine); act(engine, 'order.release', { id: order.id });
  const task = engine.state.tasks[0];
  assert.equal(task.status, 'pending');
  assert.throws(() => act(engine, 'task.transition', { id: task.id, status: 'completed' }), /禁止直接篡改/);
  engine.dispatch(); assert.equal(engine.get('tasks', task.id).status, 'assigned');
  engine.step(); assert.equal(engine.get('tasks', task.id).status, 'running');
  const observedStatuses = new Set();
  for (let tick = 0; tick < 80; tick++) { engine.step(); observedStatuses.add(engine.state.agvs.find(agv => agv.id === engine.get('tasks', task.id).assigned_agv_id).status); }
  assert.ok(observedStatuses.has('loading')); assert.ok(observedStatuses.has('unloading'));
  assert.equal(engine.get('tasks', task.id).status, 'completed');
  assert.throws(() => act(engine, 'task.cancel', { id: task.id }), /只有待处理/);
  assert.throws(() => engine.taskStatus(engine.get('tasks', task.id), 'running', 'simulation'), /不能从 completed/);
  const statuses = engine.store.events().filter(event => event.aggregateId === task.id).map(event => event.type);
  assert.ok(statuses.indexOf('task.assigned') < statuses.indexOf('task.running'));
  assert.ok(statuses.indexOf('task.running') < statuses.indexOf('task.completed'));
});

test('AC5 单车连续路径经过真实取放货点，拒绝货架/越界/对角路径', context => {
  const engine = setup(context, { config: { agv_count: 1 } });
  const order = createOrder(engine); act(engine, 'order.release', { id: order.id });
  engine.dispatch();
  const plan = engine.state.tasks[0].plan;
  assert.equal(validatePath(engine.state.map, plan.ticks), true);
  assert.ok(plan.ticks.some(point => point.action === 'loading'));
  assert.ok(plan.ticks.some(point => point.action === 'unloading'));
  assert.throws(() => validatePath(engine.state.map, [{ x: 5, y: 4, tick: 0 }]), /货架/);
  assert.throws(() => validatePath(engine.state.map, [{ x: 0, y: 0, tick: 0 }, { x: 1, y: 1, tick: 1 }]), /每 tick/);
  checkedMoves(engine, 90);
  assert.equal(engine.state.tasks[0].status, 'completed'); assert.equal(engine.state.inventory[0].qty, 4997);
});

test('AC6 同格冲突原子失败不残留预约键，时空A*等待避让', context => {
  const engine = setup(context); const table = new ReservationTable(engine.state.map);
  const first = [{ x: 7, y: 6, tick: 0 }, { x: 7, y: 7, tick: 1 }];
  assert.equal(table.reserve('first', 1, first).ok, true);
  const size = table.entries().length;
  const conflict = table.reserve('second', 1, [{ x: 8, y: 7, tick: 0 }, { x: 7, y: 7, tick: 1 }]);
  assert.equal(conflict.reason, 'vertex_conflict'); assert.equal(table.entries().length, size);
  const path = findPath(engine.state.map, table, 'second', { x: 8, y: 7 }, { x: 7, y: 7 }, 0);
  assert.equal(path.ok, true); assert.ok(path.ticks.at(-1).tick > 1);
});

test('AC7 相向穿边被拒绝，AC8 允许首尾相接，预约到期清理', context => {
  const engine = setup(context); const table = new ReservationTable(engine.state.map);
  table.reserve('first', 1, [{ x: 7, y: 7, tick: 0 }, { x: 8, y: 7, tick: 1 }]);
  const conflict = table.reserve('second', 1, [{ x: 8, y: 7, tick: 0 }, { x: 7, y: 7, tick: 1 }]);
  assert.equal(conflict.reason, 'opposite_edge');
  const following = table.reserve('third', 1, [{ x: 6, y: 7, tick: 0 }, { x: 7, y: 7, tick: 1 }]);
  assert.equal(following.ok, true);
  table.expire(4); assert.equal(table.entries().length, 0);
});

test('AC9 三车真实目标占用产生环，回退释放预约后恢复前进', context => {
  const engine = setup(context, { config: { agv_count: 3 } });
  const positions = [{ x: 7, y: 5 }, { x: 8, y: 5 }, { x: 8, y: 6 }];
  engine.state.agvs.forEach((agv, index) => { Object.assign(agv, positions[index]); agv.home = { ...positions[index] }; agv.history = [{ ...positions[index], tick: 0 }]; });
  engine.rebuildReservations();
  const graph = {};
  for (let index = 0; index < 3; index++) {
    const agv = engine.state.agvs[index]; const target = engine.state.agvs[(index + 1) % 3];
    const result = findPath(engine.state.map, engine.reservations, agv.id, agv, target, 0, { horizon: 8 });
    assert.equal(result.ok, false); assert.ok(result.blockers.includes(target.id));
    graph[agv.id] = [target.id];
  }
  assert.equal(cyclesOf(graph).length, 1);
  assert.equal(engine.recoverDeadlock(graph), true);
  assert.equal(engine.state.metrics.recoveries, 0);
  const retreating = engine.state.agvs.find(agv => agv.path.length);
  const start = cell(retreating);
  checkedMoves(engine, 2);
  assert.equal(engine.state.metrics.recoveries, 1);
  assert.notEqual(cell(retreating), start);
  assert.equal(engine.store.events().filter(event => event.type === 'deadlock.retreat').length, 1);
  for (const index of [2, 1, 0]) {
    const agv = engine.state.agvs[index];
    const target = positions[(index + 1) % positions.length];
    assert.equal(engine.route(agv, [target]).ok, true);
  }
  checkedMoves(engine, 40);
  for (let index = 0; index < positions.length; index++) assert.equal(cell(engine.state.agvs[index]), cell(positions[(index + 1) % positions.length]));
});

test('AC10 运行中封闭未来路径，地图不可变且计划版本递增', context => {
  const engine = setup(context); act(engine, 'demo.orders'); engine.step();
  const task = engine.state.tasks.find(task => task.status === 'running');
  const agv = engine.get('agvs', task.assigned_agv_id);
  const point = agv.path.find(point => engine.state.map.nodes.find(node => cell(node) === cell(point))?.type === 'floor' && !engine.state.agvs.some(other => cell(other) === cell(point) || cell(other.home) === cell(point)));
  assert.ok(point);
  const oldMap = structuredClone(engine.state.map); const oldPlan = task.plan_version;
  act(engine, 'inject.block', point);
  assert.notEqual(engine.state.map.version, oldMap.version);
  assert.deepEqual(engine.state.maps.find(map => map.version === oldMap.version), oldMap);
  assert.ok(engine.get('tasks', task.id).plan_version > oldPlan);
  checkedMoves(engine, 120);
  assert.ok(engine.state.agvs.every(agv => !agv.history.some(entry => entry.tick > 1 && cell(entry) === cell(point))));
  assert.ok(engine.state.tasks.every(task => task.status === 'completed'));
});

test('AC11 低电量充电排队、容量约束、充满离开充电位', context => {
  const engine = setup(context, { config: { agv_count: 3 } });
  for (const agv of engine.state.agvs) agv.battery = 10;
  for (let tick = 0; tick < 150; tick++) {
    engine.step();
    for (const charger of engine.state.locations.filter(location => location.type === 'charger')) {
      assert.ok(engine.state.agvs.filter(agv => agv.charging_target === charger.id).length <= charger.capacity);
      assert.ok(engine.state.agvs.filter(agv => cell(agv) === cell(charger)).length <= 1);
    }
  }
  assert.ok(engine.state.agvs.every(agv => agv.battery > 90 && agv.status === 'idle' && cell(agv) === cell(agv.home)));
  assert.equal(engine.store.events().filter(event => event.type === 'agv.charge.completed').length, 3);
});

test('AC12 急单比已排队低优先级任务先指派', context => {
  const engine = setup(context, { config: { agv_count: 1 } });
  const low = createOrder(engine, { priority: 1 }); act(engine, 'order.release', { id: low.id });
  const urgent = act(engine, 'inject.urgent')[0];
  engine.step();
  assert.equal(engine.state.tasks.find(task => task.order_id === urgent.id).status, 'running');
  assert.equal(engine.state.tasks.find(task => task.order_id === low.id).status, 'pending');
  checkedMoves(engine, 120); assert.ok(engine.state.tasks.every(task => task.status === 'completed'));
});

test('AC13 同seed和输入三次关键事件与业务结果完全一致', () => {
  const results = [];
  for (let iteration = 0; iteration < 3; iteration++) {
    const engine = new Engine({ seed: 12345, recording: false });
    act(engine, 'demo.orders'); advance(engine, 100);
    results.push({ events: engine.store.events(), tasks: engine.state.tasks, inventory: engine.state.inventory });
    engine.close();
  }
  assert.deepEqual(results[0], results[1]); assert.deepEqual(results[1], results[2]);
});

test('AC15 暂停、单步、继续、速度校验，回放保存真实事件顺序', context => {
  const engine = setup(context, { recording: true });
  act(engine, 'demo.orders'); act(engine, 'sim.step'); assert.equal(engine.state.tick, 1);
  act(engine, 'sim.start'); assert.equal(engine.state.running, true);
  assert.throws(() => act(engine, 'sim.step'), /先暂停/);
  act(engine, 'sim.pause'); act(engine, 'sim.speed', { speed: 8 }); assert.equal(engine.state.speed, 8);
  assert.throws(() => act(engine, 'sim.speed', { speed: 3 }), /只允许/);
  advance(engine, 60);
  const frames = engine.replay().frames;
  assert.equal(frames.at(-1).tick, engine.state.tick);
  assert.deepEqual(frames.at(-1).agvs, engine.state.agvs);
  const eventIds = [...new Map(frames.flatMap(frame => frame.events).map(event => [event.id, event])).values()].map(event => event.id);
  assert.deepEqual(eventIds, engine.store.events().map(event => event.id));
});

test('AC16 独立Node进程关闭重启恢复SKU/库存/订单/任务/路径快照', context => {
  const root = resolve('runtime'); mkdirSync(root, { recursive: true });
  const directory = mkdtempSync(resolve(root, 'restart-test-')); const filename = resolve(directory, 'restart.db');
  context.after(() => rmSync(directory, { recursive: true, force: true }));
  const engine = new Engine({ filename, recording: true });
  assert.throws(() => new Engine({ filename }), /数据库已被进程/);
  act(engine, 'demo.orders'); advance(engine, 3); const expected = engine.snapshot(); engine.close();
  const child = spawnSync(process.execPath, ['--input-type=module', '-e', "import {Engine} from './src/engine.js';const engine=new Engine({filename:process.env.TEST_WMS_FILE});console.log(JSON.stringify(engine.snapshot()));engine.close();"], { cwd: process.cwd(), env: { ...process.env, TEST_WMS_FILE: filename }, encoding: 'utf8' });
  assert.equal(child.status, 0, child.stderr);
  const restored = JSON.parse(child.stdout.trim());
  for (const collection of ['skus', 'locations', 'inventory', 'orders', 'tasks', 'agvs']) assert.deepEqual(restored[collection], expected[collection]);
  assert.equal(restored.tick, 3); assert.equal(restored.running, false);
  const resumed = new Engine({ filename, recording: false });
  checkedMoves(resumed, 100); assert.equal(resumed.stats().completed, 6); resumed.close();
});

test('AC17 订单/任务/事件状态、单号、AGV、优先级和UTC日期组合筛选', context => {
  const engine = setup(context); act(engine, 'demo.orders'); advance(engine, 70);
  const task = engine.state.tasks[0];
  const tasks = engine.query('tasks', { status: 'completed', priority: task.priority, agv_id: task.assigned_agv_id });
  assert.ok(tasks.some(item => item.id === task.id));
  assert.ok(tasks.every(item => item.status === 'completed' && item.priority === task.priority && item.assigned_agv_id === task.assigned_agv_id));
  const events = engine.query('events', { agv_id: task.assigned_agv_id, from_tick: 1, to_tick: 30, from_date: '2026-11-19', to_date: '2026-11-20' });
  assert.ok(events.length > 0); assert.ok(events.every(event => event.tick >= 1 && event.tick <= 30));
  const orders = engine.query('orders', { status: 'completed', code: engine.state.orders[0].code });
  assert.equal(orders.length, 1);
});

test('AC18 统计、取消数、利用率、吞吐、耗时与库存流水守恒', context => {
  const engine = setup(context); act(engine, 'demo.orders');
  const extra = createOrder(engine); act(engine, 'order.release', { id: extra.id });
  act(engine, 'task.cancel', { id: engine.state.tasks.at(-1).id }); advance(engine, 100);
  const stats = engine.stats();
  assert.equal(stats.agvs, 10); assert.equal(stats.orders, 7); assert.equal(stats.tasks, 7); assert.equal(stats.completed, 6); assert.equal(stats.cancelled, 1);
  assert.equal(stats.throughput, 216);
  assert.equal(stats.averageTaskTicks, engine.state.tasks.filter(task => task.status === 'completed').reduce((sum, task) => sum + task.completed_tick - task.created_tick, 0) / 6);
  assert.equal(stats.utilization.reduce((sum, agv) => sum + agv.completed, 0), 6);
  for (const agv of stats.utilization) assert.ok(agv.utilization >= 0 && agv.utilization <= 1);
  for (const stock of engine.state.inventory) {
    const rows = engine.state.ledger.filter(row => row.inventory_id === stock.id);
    assert.equal(rows.reduce((sum, row) => sum + row.qty_delta, 0), stock.qty);
    assert.equal(rows.reduce((sum, row) => sum + row.reserved_delta, 0), stock.reserved_qty);
  }
});

test('故障重派：assigned撤销回pending，running失败保留终态并建替代任务', context => {
  const engine = setup(context); act(engine, 'demo.orders'); engine.dispatch();
  const assigned = engine.state.tasks[0];
  const vehicleId = assigned.assigned_agv_id;
  act(engine, 'inject.fault', { agv_id: vehicleId });
  assert.equal(engine.get('tasks', assigned.id).status, 'pending');
  engine.step();
  const running = engine.state.tasks.find(task => task.status === 'running');
  const runningId = running.id;
  act(engine, 'inject.fault', { agv_id: running.assigned_agv_id });
  assert.equal(engine.get('tasks', runningId).status, 'failed');
  checkedMoves(engine, 180);
  assert.ok(engine.state.tasks.some(task => task.status === 'completed' && task.retry_count > 0));
  assert.ok(engine.state.inventory.every(stock => stock.reserved_qty === 0));
});

test('日期、容量、状态、电量与旧计划校验，库位迁移更新旧格', context => {
  const engine = setup(context);
  assert.throws(() => act(engine, 'inventory.add', { sku_id: engine.state.skus[0].id, location_id: engine.state.locations[0].id, qty: 1, expire_at: '2026-02-30' }), /日期不存在/);
  assert.throws(() => act(engine, 'order.create', { code: 'BAD', lines: [], priority: 5 }), /明细/);
  const location = act(engine, 'location.create', { code: 'MOVE', zone: 'B', x: 19, y: 12, type: 'pick', capacity: 10 });
  act(engine, 'location.update', { id: location.id, x: 20, y: 12 });
  assert.equal(engine.state.map.nodes.find(node => node.x === 19 && node.y === 12).type, 'floor');
  assert.equal(engine.state.map.nodes.find(node => node.x === 20 && node.y === 12).type, 'pick');
  assert.throws(() => act(engine, 'inject.block', { x: engine.state.agvs[0].x, y: engine.state.agvs[0].y }), /车辆所在格/);
  const oldMap = structuredClone(engine.state.map);
  assert.ok(oldMap.edges.every(edge => typeof edge.bidirectional === 'boolean'));
  rebuildEdges(oldMap);
});

test('回归：取消多行订单任务后停车预约安全且重启可恢复', context => {
  const engine = setup(context);
  const order = createOrder(engine, { lines: Array.from({ length: 11 }, () => ({ sku_id: engine.state.skus[0].id, qty: 1 })) });
  act(engine, 'order.release', { id: order.id }); advance(engine, 15);
  const pending = engine.state.tasks.find(task => task.status === 'pending');
  assert.ok(pending); act(engine, 'task.cancel', { id: pending.id });
  engine.rebuildReservations(); checkedMoves(engine, 20);
  assert.equal(engine.state.inventory[0].reserved_qty, 0);
});

test('回归：替代库存到期后仍保留故障事实并给出重派失败原因', context => {
  const engine = setup(context, { config: { agv_count: 1 } });
  const sku = act(engine, 'sku.create', { code: 'EXPIRING', name: '即将过期商品', batch_rule: 'fefo' });
  act(engine, 'inventory.add', { sku_id: sku.id, location_id: engine.state.locations[0].id, qty: 3, expire_at: '2026-11-19T00:00:01.000Z' });
  const order = createOrder(engine, { lines: [{ sku_id: sku.id, qty: 3 }] }); act(engine, 'order.release', { id: order.id }); engine.step();
  act(engine, 'inject.fault', { agv_id: engine.state.agvs[0].id });
  assert.equal(engine.state.agvs[0].status, 'fault');
  assert.equal(engine.get('orders', order.id).status, 'failed');
  assert.ok(engine.store.events().some(event => event.type === 'task.reassignment.failed'));
  assert.equal(engine.state.inventory.at(-1).reserved_qty, 0);
});

test('回归：已预占订单放货点不能改为货架', context => {
  const engine = setup(context); const order = createOrder(engine); act(engine, 'order.allocate', { id: order.id });
  const drop = engine.get('locations', order.drop_location_id);
  assert.throws(() => act(engine, 'location.update', { id: drop.id, type: 'shelf' }), /必须保持 drop/);
  act(engine, 'order.release', { id: order.id }); checkedMoves(engine, 90);
  assert.equal(engine.get('orders', order.id).status, 'completed');
});

test('回归：封路不打断已在桩上的充电，充满正常回岗', context => {
  const engine = setup(context, { config: { agv_count: 1 } }); engine.state.agvs[0].battery = 10;
  advance(engine, 18);
  const charger = engine.get('locations', engine.state.agvs[0].charging_target);
  assert.equal(cell(engine.state.agvs[0]), cell(charger));
  act(engine, 'inject.block', { x: 7, y: 7 }); checkedMoves(engine, 100);
  assert.equal(engine.state.agvs[0].status, 'idle'); assert.ok(engine.state.agvs[0].battery > 90);
  assert.equal(cell(engine.state.agvs[0]), cell(engine.state.agvs[0].home));
});

test('在线竞争演示实际驱动两车，告警含真实竞争原因', context => {
  const engine = setup(context); const result = act(engine, 'demo.conflict');
  assert.equal(result.ok, true); assert.ok(result.conflict);
  const before = result.vehicles.map(id => cell(engine.get('agvs', id)));
  checkedMoves(engine, 10);
  assert.ok(result.vehicles.some((id, index) => cell(engine.get('agvs', id)) !== before[index]));
  checkedMoves(engine, 70);
  assert.ok(result.vehicles.every(id => { const agv = engine.get('agvs', id); return cell(agv) === cell(agv.home); }));
});

test('有界恢复：回退次数耗尽不再追加路线，等待超限明确失败并释放库存', context => {
  const engine = setup(context, { config: { agv_count: 3, max_wait_ticks: 2, max_retries: 1 } });
  const agvs = engine.state.agvs;
  for (const agv of agvs) agv.retreat_count = 1;
  const graph = Object.fromEntries(agvs.map((agv, index) => [agv.id, [agvs[(index + 1) % 3].id]]));
  assert.equal(engine.recoverDeadlock(graph), false);
  assert.ok(agvs.every(agv => agv.path.length === 0));
  const order = createOrder(engine); act(engine, 'order.release', { id: order.id });
  for (const agv of engine.state.agvs) act(engine, 'inject.offline', { agv_id: agv.id });
  advance(engine, 6);
  assert.equal(engine.get('orders', order.id).status, 'failed');
  assert.equal(engine.state.inventory[0].reserved_qty, 0);
  assert.ok(engine.state.events.some(event => event.type === 'order.failure.reason' && event.payload.reason.includes('超时')));
});

test('回归：多车连带超时后跳过失效循环条目，不回滚失败事实', context => {
  const engine = setup(context, { config: { agv_count: 2, max_wait_ticks: 2 } });
  const order = createOrder(engine, { lines: engine.state.skus.slice(0, 2).map(sku => ({ sku_id: sku.id, qty: 1 })) });
  act(engine, 'order.release', { id: order.id }); engine.step();
  for (const point of [{ x: 3, y: 13 }, { x: 5, y: 13 }, { x: 4, y: 12 }, { x: 4, y: 14 }]) act(engine, 'inject.block', point);
  checkedMoves(engine, 7);
  assert.equal(engine.get('orders', order.id).status, 'failed');
  assert.ok(engine.state.inventory.every(stock => stock.reserved_qty === 0));
});

test('回归：回退中故障不得虚记死锁恢复', context => {
  const engine = setup(context); act(engine, 'demo.deadlock');
  const vehicle = engine.state.agvs.find(agv => agv.recovery_pending);
  assert.ok(vehicle); const initial = cell(vehicle);
  act(engine, 'inject.fault', { agv_id: vehicle.id }); engine.step();
  assert.equal(cell(engine.get('agvs', vehicle.id)), initial);
  assert.equal(engine.state.metrics.recoveries, 0);
  assert.ok(!engine.store.events().some(event => event.type === 'deadlock.recovered'));
});

test('FR7 局部重规划：无关封路及解封保留全部原路径与任务计划', context => {
  const engine = setup(context); act(engine, 'demo.orders'); engine.step();
  const plans = structuredClone(engine.state.tasks.map(task => ({ id: task.id, plan_version: task.plan_version, plan: task.plan })));
  const routes = structuredClone(engine.state.agvs.map(agv => ({ id: agv.id, path: agv.path, plan_version: agv.plan_version })));
  for (const action of ['inject.block', 'inject.unblock']) {
    act(engine, action, { x: 0, y: 0 });
    assert.deepEqual(engine.state.tasks.map(task => ({ id: task.id, plan_version: task.plan_version, plan: task.plan })), plans);
    assert.deepEqual(engine.state.agvs.map(agv => ({ id: agv.id, path: agv.path, plan_version: agv.plan_version })), routes);
    assert.deepEqual(engine.state.events.findLast(event => event.type === 'map.routes.revalidated').payload.replanned_agv_ids, []);
  }
  checkedMoves(engine, 90); assert.ok(engine.state.tasks.every(task => task.status === 'completed'));
});

test('FR7 局部重规划：受影响车避障，依赖范围外计划与预约保留', context => {
  const engine = setup(context); act(engine, 'demo.orders'); engine.step();
  const before = structuredClone(engine.state.agvs);
  const taskPlans = structuredClone(engine.state.tasks);
  const point = before.flatMap(agv => agv.path).find(point =>
    engine.state.map.nodes.some(node => cell(node) === cell(point) && node.type === 'floor') &&
    !before.some(agv => cell(agv) === cell(point) || cell(agv.home) === cell(point)));
  assert.ok(point); act(engine, 'inject.block', point);
  const result = engine.state.events.findLast(event => event.type === 'map.routes.revalidated').payload;
  const changed = new Set(result.replanned_agv_ids);
  assert.ok(changed.size > 0 && changed.size < before.length);
  for (const old of before.filter(agv => !changed.has(agv.id))) {
    const current = engine.get('agvs', old.id);
    assert.deepEqual(current.path, old.path); assert.equal(current.plan_version, old.plan_version);
    if (old.active_task_id) assert.deepEqual(engine.get('tasks', old.active_task_id), taskPlans.find(task => task.id === old.active_task_id));
  }
  engine.rebuildReservations(); checkedMoves(engine, 120);
  assert.ok(engine.state.tasks.every(task => task.status === 'completed'));
  assert.ok(engine.state.agvs.every(agv => !agv.history.some(entry => entry.tick > 1 && cell(entry) === cell(point))));
});

test('FR7 局部重规划：清除原路径后的停车冲突按依赖递归扩展', context => {
  const engine = setup(context, { config: { agv_count: 4 } });
  const [a, b, c, untouched] = engine.state.agvs;
  Object.assign(a, { x: 7, y: 5, path: [{ x: 7, y: 6, tick: 1 }, { x: 7, y: 7, tick: 2 }] });
  Object.assign(b, { x: 8, y: 5, path: [{ x: 7, y: 5, tick: 1 }] });
  Object.assign(c, { x: 8, y: 4, path: [{ x: 8, y: 5, tick: 1 }] });
  engine.rebuildReservations(); const original = structuredClone(untouched);
  act(engine, 'inject.block', { x: 7, y: 6 });
  const result = engine.state.events.findLast(event => event.type === 'map.routes.revalidated').payload;
  assert.deepEqual(new Set(result.replanned_agv_ids), new Set([a.id, b.id, c.id]));
  assert.deepEqual(engine.get('agvs', untouched.id), original);
  engine.rebuildReservations(); checkedMoves(engine, 80);
});

test('AC17 回归：完整历史日志跨200条后按车辆、任务、UTC日期查询及分页', async context => {
  const engine = new Engine({ recording: false });
  const app = createApplication({ engine }); context.after(() => app.close());
  await new Promise(resolveReady => app.server.listen(0, '127.0.0.1', resolveReady));
  act(engine, 'demo.orders'); advance(engine, 50);
  const all = engine.query('events'); assert.ok(all.length > 200);
  const early = all.find(event => event.type === 'task.assigned');
  assert.ok(!engine.snapshot().events.some(event => event.id === early.id));
  const base = `http://127.0.0.1:${app.server.address().port}`;
  const params = new URLSearchParams({ agv_id: early.payload.agv_id, task_id: early.aggregateId, type: early.type,
    from_tick: '0', to_tick: String(early.tick), from_date: '2026-11-19', to_date: '2026-11-20', search: 'ASSIGNED' });
  const response = await fetch(`${base}/api/events/page?${params}`); assert.equal(response.status, 200);
  const page = await response.json(); assert.deepEqual(page.items.map(event => event.id), [early.id]);
  assert.equal(page.total, 1); assert.ok(page.types.includes('simulation.reset'));
  const ids = [];
  for (let offset = 0; offset < all.length; offset += 40) {
    const result = await fetch(`${base}/api/events/page?offset=${offset}&limit=40`).then(response => response.json());
    assert.equal(result.total, all.length); assert.ok(result.items.length <= 40);
    ids.push(...result.items.map(event => event.id));
  }
  assert.deepEqual(ids, all.reverse().map(event => event.id));
  for (const invalid of ['limit=0', 'offset=-1', 'from_tick=9&to_tick=2', 'from_date=2026-11-20&to_date=2026-11-19']) {
    const invalidResponse = await fetch(`${base}/api/events/page?${invalid}`); assert.equal(invalidResponse.status, 400);
  }
  assert.ok(Array.isArray(await fetch(`${base}/api/events`).then(response => response.json())));
});
