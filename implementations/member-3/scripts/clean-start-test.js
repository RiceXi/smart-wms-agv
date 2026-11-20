import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

// Only the documented application files are copied; existing runtime data is excluded.
const root = fileURLToPath(new URL('../', import.meta.url));
mkdirSync(resolve(root, 'runtime'), { recursive: true });
const isolated = mkdtempSync(resolve(root, 'runtime/clean-start-'));
const files = ['package.json', 'src', 'public', 'scenarios'];
for (const name of files) cpSync(resolve(root, name), resolve(isolated, name), { recursive: true });
const portProbe = createServer();
await new Promise(resolveReady => portProbe.listen(0, '127.0.0.1', resolveReady));
const port = portProbe.address().port;
await new Promise(resolveClose => portProbe.close(resolveClose));
const child = spawn(process.execPath, ['src/server.js'], {
  cwd: isolated, env: { ...process.env, PORT: String(port), WMS_DB: resolve(isolated, 'runtime/wms-member3.db') },
  windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']
});
let output = '';
child.stdout.on('data', data => { output += data.toString(); });
child.stderr.on('data', data => { output += data.toString(); });
const closed = new Promise(resolveClose => child.once('close', resolveClose));
try {
  const base = `http://127.0.0.1:${port}`;
  let health;
  for (let attempt = 0; attempt < 100; attempt++) {
    try { health = await fetch(`${base}/health`).then(response => response.json()); break; }
    catch { if (child.exitCode !== null) throw new Error(output); await new Promise(resolveDelay => setTimeout(resolveDelay, 100)); }
  }
  assert.equal(health?.status, 'ok', output);
  const snapshot = await fetch(`${base}/api/snapshot`).then(response => response.json());
  assert.equal(snapshot.skus.length, 3);
  assert.equal(snapshot.locations.length, 9);
  assert.equal(snapshot.inventory.length, 3);
  assert.equal(snapshot.agvs.length, 10);
  assert.equal(snapshot.tick, 0);
  assert.equal(snapshot.running, false);
  const assets = {};
  for (const name of ['/', '/app.js', '/styles.css']) {
    const response = await fetch(base + name);
    assert.equal(response.status, 200); assert.ok((await response.text()).length > 100);
    assets[name] = response.status;
  }
  const manifest = JSON.parse(readFileSync(resolve(isolated, 'package.json'), 'utf8'));
  assert.equal(Object.keys(manifest.dependencies ?? {}).length, 0);
  const report = { measuredAt: new Date().toISOString(), runtime: process.version, copiedFiles: files,
    thirdPartyDependencies: 0, dependencyInstallRequired: false, health, assets,
    counts: { skus: snapshot.skus.length, locations: snapshot.locations.length, inventory: snapshot.inventory.length, agvs: snapshot.agvs.length },
    freshDatabaseCreated: true, passed: true,
    scope: '在本机已有Node24环境，把公开运行文件复制到个人runtime隔离目录，从空库启动真实HTTP子进程；不代表无Node机器或其他操作系统测试。' };
  mkdirSync(resolve(root, 'evidence'), { recursive: true });
  writeFileSync(resolve(root, 'evidence/clean-start-results.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally {
  if (child.exitCode === null) child.kill('SIGTERM');
  await closed;
}
