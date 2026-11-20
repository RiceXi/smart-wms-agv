import { performance } from 'node:perf_hooks';
import { mkdirSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';
import { Engine } from '../src/engine.js';
import { ReservationTable, findPath } from '../src/planner.js';
import { rebuildEdges } from '../src/map-engine.js';
import { cell } from '../src/common.js';

const started = performance.now();
const hardware = { os: `${os.type()} ${os.release()}`, cpu: os.cpus()[0].model, logicalProcessors: os.cpus().length, memoryBytes: os.totalmem(), runtime: process.version };
const conflictStarted = performance.now();
const map = rebuildEdges({ id: 'benchmark-grid', version: 'v1', width: 9, height: 9, nodes: Array.from({ length: 81 }, (_, index) => ({ x: index % 9, y: Math.floor(index / 9), type: 'floor', capacity: 1, walkable: true })) });
let solved = 0;
let rejected = 0;
const failures = [];
for (let index = 0; index < 500; index++) {
  const x = 2 + index % 5; const y = 2 + Math.floor(index / 5) % 5; const tick = index * 3;
  const table = new ReservationTable(map);
  const startA = { x, y, tick }; const targetA = { x: x + 1, y, tick: tick + 1 };
  const startB = index % 2 ? { x: x + 1, y, tick } : { x: x + 1, y: y + 1, tick };
  const targetB = index % 2 ? { x, y, tick: tick + 1 } : targetA;
  assert.equal(table.reserve('A', 1, [startA, targetA]).ok, true);
  const before = table.entries().length;
  const result = table.reserve('B', 1, [startB, targetB]);
  if (!result.ok) rejected++;
  assert.equal(table.entries().length, before);
  const replanned = findPath(map, table, 'B', startB, targetB, tick, { horizon: 20 });
  if (replanned.ok && table.reserve('B', 2, replanned.ticks).ok) solved++;
  else failures.push({ index, reason: replanned.reason });
}
const conflictCases = { scenarios: 500, vertexCases: 250, oppositeEdgeCases: 250, rejectedOriginalConflict: rejected, solved, successRate: solved / 500, elapsedMs: performance.now() - conflictStarted,
  scope: '固定9x9网格局部双车竞争微场景；不代表任意仓库拓扑或全车队场景的成功率', failures };
const fleets = [];
for (const agvCount of [10, 30]) {
  const engine = new Engine({ config: { agv_count: agvCount }, recording: false });
  const fleetStarted = performance.now();
  const ticks = Number(process.env.BENCH_TICKS ?? 1800);
  let invariantChecks = 0;
  for (let tick = 0; tick < ticks; tick++) {
    if (tick % 120 === 0 && tick < 1200) engine.command({ action: 'demo.orders' });
    const previous = engine.state.agvs.map(agv => ({ x: agv.x, y: agv.y }));
    engine.step();
    for (let index = 0; index < agvCount; index++) for (let other = index + 1; other < agvCount; other++) {
      assert.ok(cell(engine.state.agvs[index]) !== cell(engine.state.agvs[other]));
      assert.ok(!(cell(previous[index]) === cell(engine.state.agvs[other]) && cell(previous[other]) === cell(engine.state.agvs[index])));
      invariantChecks++;
    }
  }
  const delays = [...engine.state.metrics.dispatchMs].sort((left, right) => left - right);
  fleets.push({ agvCount, ticks, simulatedSeconds: ticks, elapsedMs: performance.now() - fleetStarted,
    taskCount: engine.state.tasks.length, completed: engine.stats().completed, failed: engine.state.tasks.filter(task => task.status === 'failed').length,
    unfinished: engine.state.tasks.filter(task => !['completed', 'cancelled', 'failed'].includes(task.status)).length,
    assignmentSamples: delays.length, assignmentP95Ms: delays[Math.max(0, Math.ceil(delays.length * 0.95) - 1)] ?? null,
    invariantChecks, safetyViolations: 0, stats: engine.stats() });
  engine.close();
}
const report = { measuredAt: new Date().toISOString(), hardware, conflictCases, fleets, elapsedMs: performance.now() - started,
  wallClockThirtyMinuteSoak: '此脚本只做加速仿真，独立墙钟实验结果见soak-results.json；1800 ticks不能等同于30分钟墙钟运行。', frontendFps: '见 browser-results.json，单独以真实 requestAnimationFrame 计时。' };
mkdirSync(resolve('evidence'), { recursive: true });
writeFileSync(resolve('evidence/benchmark-results.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
assert.equal(solved, 500);
for (const fleet of fleets) assert.equal(fleet.unfinished, 0);
