import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import os from 'node:os';
import { Engine } from '../src/engine.js';
import { ReservationTable, findPath } from '../src/planner.js';
import { cell } from '../src/common.js';

const count = Number(process.env.SCENARIO_COUNT ?? 500);
assert.ok(Number.isInteger(count) && count > 0 && count <= 500);
const started = performance.now(), results = [];
const tag = count === 500 ? '2026-11-20' : `debug-${count}`;
function rng(seed) { let value = seed >>> 0; return () => { value = (Math.imul(value, 1664525) + 1013904223) >>> 0; return value / 4294967296; }; }

for (let index = 0; index < count; index++) {
  const seed = 20262000 + index, random = rng(seed), engine = new Engine({ seed, recording: false });
  const scenarioStarted = performance.now();
  const result = { index, seed, mapSize: [22, 16], agvs: 10, kind: index % 2 ? 'opposite_edge' : 'vertex_conflict',
    staticBlocks: [], orders: [], injectedBlock: null, checks: 0, safetyViolations: 0, success: false };
  try {
    // Full warehouse map; keep main stations and initial vehicle cells available.
    const candidates = engine.state.map.nodes.filter(node => node.type === 'floor' && node.y >= 5 && node.y <= 12 && [2, 3, 7, 8, 12, 13, 17, 18].includes(node.x));
    for (let n = 0; n < 1 + index % 3; n++) {
      const point = candidates.splice(Math.floor(random() * candidates.length), 1)[0];
      engine.command({ action: 'inject.block', x: point.x, y: point.y });
      result.staticBlocks.push({ x: point.x, y: point.y });
    }
    const pairIndex = Math.floor(random() * 9);
    const [first, second] = engine.state.agvs.slice(pairIndex, pairIndex + 2);
    let proposed;
    if (result.kind === 'opposite_edge') {
      // Pair setup is completed before any tick executes: B yields its old idle
      // reservation, A reserves the requested crossing, then B plans safely.
      engine.reservations.release(second.id);
      assert.equal(engine.route(first, [{ ...second.home, action: 'scenario_hold', dwell: 8 }, first.home]).ok, true);
      proposed = [{ x: second.x, y: second.y, tick: 0 }, { x: first.x, y: first.y, tick: 1 }];
      result.target = { ...first.home };
    } else {
      const target = { x: first.x + 1, y: 2 + index % 2, action: 'scenario_hold', dwell: 12 + Math.floor(random() * 9) };
      assert.equal(engine.route(first, [target, first.home]).ok, true);
      const naive = findPath(engine.state.map, new ReservationTable(engine.state.map), second.id, second, target, 0);
      assert.equal(naive.ok, true);
      proposed = [...naive.ticks];
      for (let n = 0; n < target.dwell; n++) proposed.push({ x: target.x, y: target.y, tick: proposed.at(-1).tick + 1 });
      result.target = target;
    }
    const before = engine.reservations.entries();
    const rejection = engine.reservations.reserve(second.id, second.plan_version + 1, proposed);
    assert.equal(rejection.ok, false);
    assert.equal(rejection.reason, result.kind);
    assert.deepEqual(engine.reservations.entries(), before, 'failed reservation must remain atomic');
    result.rejection = rejection;
    const goals = result.kind === 'opposite_edge' ? [first.home, second.home] : [result.target, second.home];
    const reroute = engine.route(second, goals);
    assert.equal(reroute.ok, true);
    result.resolvedRoute = reroute.ticks;
    engine.state.metrics.conflicts++;
    engine.event('conflict.scenario.resolved', second.id, { reason: rejection.reason, seed });
    engine.rebuildReservations(); engine.assertInvariants();

    const drops = engine.state.locations.filter(location => location.type === 'drop');
    for (let n = 0; n < 3 + index % 6; n++) {
      const fields = { code: `SC-${seed}-${n}`, requester: 'scenario-runner', priority: 1 + Math.floor(random() * 10),
        drop_location_id: drops[Math.floor(random() * drops.length)].id,
        lines: [{ sku_id: engine.state.skus[Math.floor(random() * 3)].id, qty: 1 + Math.floor(random() * 5) }] };
      const order = engine.command({ action: 'order.create', ...fields });
      engine.command({ action: 'order.release', id: order.id }); result.orders.push(fields);
    }
    const bound = 240;
    while (engine.state.tick < bound) {
      const previous = engine.state.agvs.map(agv => ({ x: agv.x, y: agv.y }));
      engine.step();
      for (let a = 0; a < 10; a++) for (let b = a + 1; b < 10; b++) {
        const [left, right] = [engine.state.agvs[a], engine.state.agvs[b]];
        const safe = cell(left) !== cell(right) && !(cell(left) === cell(previous[b]) && cell(right) === cell(previous[a]));
        result.checks++;
        if (!safe) result.safetyViolations++;
        assert.ok(safe, `unsafe movement at tick ${engine.state.tick}`);
      }
      if (engine.state.tick === 8) {
        const occupied = new Set(engine.state.agvs.flatMap(agv => [cell(agv), cell(agv.home)]));
        const future = engine.state.agvs.flatMap(agv => agv.path).filter(point => point.tick > 11 && point.y > 2 && !occupied.has(cell(point)));
        const point = future.find(point => engine.state.map.nodes.some(node => cell(node) === cell(point) && node.type === 'floor'));
        assert.ok(point, 'scenario must include a live route closure');
        result.injectedBlock = { x: point.x, y: point.y };
        engine.command({ action: 'inject.block', ...result.injectedBlock });
      }
      if (engine.state.tick === 15 && index % 5 === 0) engine.command({ action: 'inject.urgent' });
      if (engine.state.tick === 28) engine.command({ action: 'inject.unblock', ...result.injectedBlock });
      if (engine.state.tick >= 30 && engine.state.tasks.every(task => task.status === 'completed') && engine.state.agvs.every(agv => !agv.path.length)) break;
    }
    result.ticks = engine.state.tick;
    result.taskCount = engine.state.tasks.length;
    result.completed = engine.stats().completed;
    result.failed = engine.state.tasks.filter(task => task.status === 'failed').length;
    result.unfinished = engine.state.tasks.filter(task => !['completed', 'failed', 'cancelled'].includes(task.status)).length;
    result.metrics = engine.stats();
    assert.equal(result.completed, result.taskCount, 'all released tasks must actually complete');
    assert.ok(engine.state.agvs.every(agv => !agv.path.length), 'fleet must return to idle');
    result.success = true;
  } catch (error) {
    result.error = { message: error.message, stack: error.stack, tick: engine.state.tick };
    result.taskStates = engine.state.tasks.map(task => ({ id: task.id, status: task.status }));
  } finally { engine.close(); }
  result.elapsedMs = performance.now() - scenarioStarted;
  results.push(result);
  if ((index + 1) % 25 === 0) console.log(JSON.stringify({ completedScenarios: index + 1, successes: results.filter(item => item.success).length, elapsedMs: performance.now() - started }));
}
const successes = results.filter(item => item.success).length;
const report = { measuredAt: new Date().toISOString(), hardware: { os: `${os.type()} ${os.release()}`, cpu: os.cpus()[0].model, logicalProcessors: os.cpus().length, memoryBytes: os.totalmem(), runtime: process.version },
  scenarios: count, successes, successRate: successes / count, rejectedOriginalConflict: results.filter(item => item.rejection?.ok === false).length,
  vertexScenarios: results.filter(item => item.kind === 'vertex_conflict').length, oppositeEdgeScenarios: results.filter(item => item.kind === 'opposite_edge').length,
  totalTasks: results.reduce((sum, item) => sum + (item.taskCount ?? 0), 0), completedTasks: results.reduce((sum, item) => sum + (item.completed ?? 0), 0),
  safetyChecks: results.reduce((sum, item) => sum + item.checks, 0), safetyViolations: results.reduce((sum, item) => sum + item.safetyViolations, 0),
  elapsedMs: performance.now() - started,
  scope: `${count} fixed-seed full 22x16 warehouse Engine fixtures with 10 physical AGVs, 1-3 initial closures, live route closure/unblock, varied orders/priority/drop points and periodic urgent orders. Controlled online pair requests seed vertex and opposite-edge conflicts before tick execution; all vehicles then move through real Engine ticks with WMS traffic. In-memory SQLite, replay disabled, accelerated simulation. Not arbitrary topology or natural deadlock success probability.`,
  failures: results.filter(item => !item.success).map(item => ({ index: item.index, seed: item.seed, error: item.error })), results };
mkdirSync(resolve('evidence'), { recursive: true });
const filename = resolve(`evidence/conflict-scenarios-${tag}.json`);
writeFileSync(filename, JSON.stringify(report, null, 2));
console.log(JSON.stringify({ ...report, results: undefined, filename }, null, 2));
assert.ok(report.successRate >= .95, 'fixed denominator success rate must reach 95%');
assert.equal(report.safetyViolations, 0);
