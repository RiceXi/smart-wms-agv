import { performance } from 'node:perf_hooks';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { Engine } from '../src/engine.js';
import assert from 'node:assert/strict';

const durationSeconds = Number(process.env.SOAK_SECONDS ?? 1800);
assert.ok(Number.isInteger(durationSeconds) && durationSeconds >= 1);
const started = performance.now();
const engine = new Engine({ recording: false });
const delay = milliseconds => new Promise(resolveDelay => setTimeout(resolveDelay, milliseconds));
let nextTickAt = started;
let maximumStepMs = 0;
let safetyChecks = 0;
let error = null;
console.log(`开始连续墙钟稳定性测试：${durationSeconds}秒，10台AGV，每60ticks创建6个任务。`);
try {
  while ((performance.now() - started) / 1000 < durationSeconds) {
    if (engine.state.tick % 60 === 0 && engine.state.tick < durationSeconds - 90) engine.command({ action: 'demo.orders' });
    const before = engine.state.agvs.map(agv => ({ x: agv.x, y: agv.y }));
    const stepStarted = performance.now();
    engine.step();
    maximumStepMs = Math.max(maximumStepMs, performance.now() - stepStarted);
    for (let index = 0; index < before.length; index++) for (let other = index + 1; other < before.length; other++) {
      const first = engine.state.agvs[index]; const second = engine.state.agvs[other];
      assert.ok(!(first.x === second.x && first.y === second.y));
      assert.ok(!(first.x === before[other].x && first.y === before[other].y && second.x === before[index].x && second.y === before[index].y));
      safetyChecks++;
    }
    if (engine.state.tick % 60 === 0) console.log(JSON.stringify({ elapsedSeconds: (performance.now() - started) / 1000, tick: engine.state.tick, completed: engine.stats().completed }));
    nextTickAt += 1000;
    await delay(Math.max(0, nextTickAt - performance.now()));
  }
} catch (failure) { error = { message: failure.message, stack: failure.stack }; process.exitCode = 1; }
const report = { measuredAt: new Date().toISOString(), runtime: process.version, requestedWallSeconds: durationSeconds,
  elapsedWallSeconds: (performance.now() - started) / 1000, ticks: engine.state.tick, agvs: engine.state.agvs.length,
  tasks: engine.state.tasks.length, completed: engine.stats().completed,
  unfinished: engine.state.tasks.filter(task => !['completed', 'cancelled', 'failed'].includes(task.status)).length,
  failed: engine.state.tasks.filter(task => task.status === 'failed').length,
  maximumStepMs, safetyChecks, safetyViolations: error ? null : 0, error,
  fullThirtyMinuteRun: durationSeconds >= 1800 && (performance.now() - started) >= 1800000,
  scope: '真实墙钟持续运行、内存SQLite、关闭逐帧回放；10台车，每60ticks创建6单。不是持久化文件长期写入或浏览器30分钟帧率实测。' };
mkdirSync(resolve('evidence'), { recursive: true });
writeFileSync(resolve('evidence/soak-results.json'), JSON.stringify(report, null, 2));
engine.close(); console.log(JSON.stringify(report, null, 2));
assert.equal(report.error, null);
assert.equal(report.unfinished, 0);
