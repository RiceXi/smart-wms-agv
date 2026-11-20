// 审查复现器：当前缺陷证据，不属于通过的验收套件；只用内存数据库。
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { writeFileSync } from 'node:fs';
import { Engine } from '../src/engine.js';
import { createApplication } from '../src/server.js';

const engine = new Engine({ config: { agv_count: 1, max_wait_ticks: 2, max_retries: 1 }, recording: false });
const app = createApplication({ engine });
const result = { checkedAt: new Date().toISOString(), database: ':memory:', browserTested: false, config: engine.config };
try {
  app.server.listen(0, '127.0.0.1');
  await once(app.server, 'listening');
  const base = `http://127.0.0.1:${app.server.address().port}`;
  const command = async (action, fields = {}) => {
    const response = await fetch(`${base}/api/command`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...fields, action }) });
    const body = await response.json();
    assert.equal(response.status, 200, JSON.stringify(body));
    return body.result;
  };
  const snapshot = async () => (await fetch(`${base}/api/snapshot`)).json();
  const sku = await command('sku.create', { code: 'REVIEW-HTTP-SHELF', name: '审查货架商品', batch_rule: 'fifo' });
  const shelf = engine.state.locations.find(location => location.code === 'SHELF-A');
  await command('inventory.add', { sku_id: sku.id, location_id: shelf.id, qty: 5 });
  const order = await command('order.create', { code: 'REVIEW-HTTP-ORDER', requester: 'review', lines: [{ sku_id: sku.id, qty: 1 }] });
  await command('order.release', { id: order.id });
  await command('inject.block', { x: 4, y: 4 });
  await command('inject.block', { x: 5, y: 3 });
  await command('sim.start');
  result.afterStart = { tick: engine.state.tick, running: engine.state.running };
  await new Promise(resolve => setTimeout(resolve, 2200));
  const stopped = await snapshot();
  result.afterTimer = { tick: stopped.tick, running: stopped.running, errors: stopped.events.filter(event => event.type === 'simulation.error'), order: stopped.orders.find(row => row.id === order.id), tasks: stopped.tasks.filter(row => row.order_id === order.id) };
  assert.equal(result.afterTimer.tick, 0);
  assert.equal(result.afterTimer.running, false);
  assert.equal(result.afterTimer.errors.length, 1);
  assert.match(result.afterTimer.errors[0].payload.message, /没有可用相邻取货点/);
  await command('inject.unblock', { x: 4, y: 4 });
  await command('sim.step');
  const resumed = await snapshot();
  result.afterManualUnblockAndStep = { tick: resumed.tick, running: resumed.running, order: resumed.orders.find(row => row.id === order.id), tasks: resumed.tasks.filter(row => row.order_id === order.id) };
  assert.equal(result.afterManualUnblockAndStep.tick, 1);
  result.defectReproduced = true;
  result.scope = '真实HTTP命令与服务定时器；未测试浏览器；手动解封后单步可推进不等于自动恢复或已验证任务完成';
} finally {
  await app.close();
}
writeFileSync(new URL('./review-http-shelf-closure-2026-11-20.json', import.meta.url), JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify({ defectReproduced: result.defectReproduced, afterStart: result.afterStart, afterTimer: { tick: result.afterTimer.tick, running: result.afterTimer.running, errors: result.afterTimer.errors.length }, afterManualUnblockTick: result.afterManualUnblockAndStep.tick }));
