import test from 'node:test';
import assert from 'node:assert/strict';
import { Engine } from '../src/engine.js';
import { createApplication } from '../src/server.js';

const faces = [{ x: 4, y: 4 }, { x: 5, y: 3 }];
function fixture(t, wait = 6, shelfAt = null) {
  const engine = new Engine({ recording: false, config: { agv_count: 1, max_wait_ticks: wait, max_retries: 1 } });
  const app = createApplication({ engine });
  t.after(() => app.close());
  const cmd = (action, fields = {}) => engine.command({ ...fields, action });
  const sku = cmd('sku.create', { code: 'SHELF-F3', name: '货架商品' });
  const shelf = shelfAt ? cmd('location.create', { code: 'CUSTOM-SHELF', zone: 'A', type: 'shelf', capacity: 5, ...shelfAt }) : engine.state.locations.find(row => row.code === 'SHELF-A');
  const stock = cmd('inventory.add', { sku_id: sku.id, location_id: shelf.id, qty: 5 });
  const release = (code, skuId, priority = 5) => {
    const { id } = cmd('order.create', { code, priority, lines: [{ sku_id: skuId, qty: 1 }] });
    cmd('order.release', { id });
    return engine.get('orders', id);
  };
  const order = release('SHELF', sku.id, 10);
  const task = engine.state.tasks.find(row => row.order_id === order.id);
  return { engine, app, cmd, release, order, task, stock: engine.get('inventory', stock.id) };
}
function finish(engine, order) {
  for (let i = 0; i < 110 && order.status === 'released'; i++) engine.step();
  assert.equal(order.status, 'completed');
}
async function listen(app) {
  await new Promise(resolve => app.server.listen(0, '127.0.0.1', resolve));
  return `http://127.0.0.1:${app.server.address().port}`;
}

test('货架全封超时只取消本单并释放一次预占，另一订单继续完成', t => {
  const { engine, cmd, release, order, task, stock } = fixture(t, 2);
  const other = release('OTHER', engine.state.skus[0].id);
  faces.forEach(point => cmd('inject.block', point));
  engine.step();
  assert.equal(task.status, 'pending');
  const otherTask = engine.state.tasks.find(row => row.order_id === other.id);
  assert.equal(otherTask.status, 'running');
  const originalPlan = structuredClone(otherTask.plan);
  for (let i = 0; i < 7; i++) engine.step();
  assert.equal(engine.state.tick, 8);
  assert.deepEqual(otherTask.plan, originalPlan);
  assert.equal(order.status, 'failed');
  assert.equal(task.status, 'cancelled');
  assert.deepEqual([stock.qty, stock.reserved_qty], [5, 0]);
  assert.equal(engine.state.ledger.filter(row => row.order_id === order.id && row.reserved_delta < 0).length, 1);
  finish(engine, other);
});

test('派车前封路，真实HTTP启动后持续推进，解封后原任务完成', async t => {
  const { app, engine, order, task, stock } = fixture(t, 60);
  const base = await listen(app);
  const command = async (action, fields = {}) => {
    const res = await fetch(`${base}/api/command`, { method: 'POST', body: JSON.stringify({ action, ...fields }) });
    assert.equal(res.status, 200);
    return (await res.json()).snapshot;
  };
  for (const point of faces) await command('inject.block', point);
  await command('sim.start');
  let state;
  const deadline = Date.now() + 8000;
  do {
    await new Promise(resolve => setTimeout(resolve, 100));
    state = await (await fetch(`${base}/api/snapshot`)).json();
  } while (state.running && state.tick < 2 && Date.now() < deadline);
  assert.ok(state.running && state.tick >= 2);
  assert.equal(state.events.filter(e => e.type === 'task.blocked' && e.aggregateId === task.id).length, 1);
  assert.ok(!state.events.some(e => e.type === 'simulation.error'));
  await command('sim.pause');
  await command('inject.unblock', faces[1]);
  for (let i = 0; i < 100 && order.status === 'released'; i++) await command('sim.step');
  assert.equal(order.status, 'completed'); assert.equal(task.status, 'completed');
  assert.deepEqual([stock.qty, stock.reserved_qty], [4, 0]);
  assert.equal(engine.state.tasks.length, 1);
});

for (const reopen of [true, false]) test(`取货途中全封，${reopen ? '解封后完成' : '超时失败并归还库存'}`, t => {
  const { engine, cmd, order, task, stock } = fixture(t, 4);
  engine.step();
  assert.equal(task.status, 'running');
  const initialVersion = task.plan_version;
  faces.forEach(point => cmd('inject.block', point));
  assert.equal(task.plan, null);
  assert.ok(task.plan_version > initialVersion);
  const agv = engine.state.agvs[0];
  const stopped = [agv.x, agv.y];
  engine.step();
  assert.deepEqual([agv.x, agv.y], stopped);
  if (reopen) {
    cmd('inject.unblock', faces[1]); finish(engine, order);
    assert.equal(stock.qty, 4);
  } else {
    for (let i = 0; i < 8; i++) engine.step();
    assert.equal(order.status, 'failed'); assert.equal(task.status, 'failed');
    assert.equal(stock.qty, 5);
  }
  assert.equal(stock.reserved_qty, 0);
});

test('首选货架面可站立但与通道隔绝时从另一面取货', t => {
  const { engine, cmd, order, task } = fixture(t, 6, { x: 18, y: 12 });
  for (const p of [{x:16,y:12},{x:17,y:11},{x:17,y:13}]) cmd('inject.block', p);
  engine.step();
  const load = task.plan.ticks.find(point => point.action === 'loading');
  assert.deepEqual([load.x, load.y], [19, 12]);
  finish(engine, order);
});

test('资源写入必须匹配文档中的方法、资源、ID和操作', async t => {
  const { app, engine, order, task } = fixture(t);
  const base = await listen(app);
  const write = (path, method, body) => fetch(base + path, { method, body: JSON.stringify(body) });
  const newSku = { code: 'ROUTE', name: '路由校验' };
  const count = engine.state.skus.length;
  assert.equal((await write('/api/skus/unused', 'POST', newSku)).status, 405);
  assert.equal(engine.state.skus.length, count);
  assert.equal((await write('/api/skus', 'PATCH', { ...newSku, id: engine.state.skus[0].id })).status, 405);
  assert.equal((await write(`/api/locations/${task.id}/priority`, 'POST', { priority: 3 })).status, 405);
  assert.equal(task.priority, 10);
  assert.equal((await write(`/api/tasks/${task.id}/priority`, 'POST', { priority: 3 })).status, 200);
  assert.equal(task.priority, 3);
  assert.equal((await write('/api/skus', 'POST', newSku)).status, 201);
  assert.equal(order.status, 'released');
});
