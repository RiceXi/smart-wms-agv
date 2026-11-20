import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve, extname, dirname, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { Engine } from './engine.js';
import { DomainError } from './common.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const publicRoot = resolve(root, 'public');
const mime = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml' };

export function createApplication(options = {}) {
  const engine = options.engine ?? new Engine({ filename: options.filename ?? resolve(root, 'runtime/wms-member3.db') });
  const subscribers = new Set();
  let closed = false;
  const broadcast = () => {
    const message = `event: snapshot\ndata: ${JSON.stringify(engine.snapshot())}\n\n`;
    for (const response of subscribers) {
      if (response.writableLength > 1024 * 1024) { response.destroy(); subscribers.delete(response); }
      else response.write(message);
    }
  };
  const json = (response, status, body) => {
    response.writeHead(status, { 'Content-Type': mime['.json'], 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
    response.end(JSON.stringify(body));
  };
  const readBody = async request => {
    const chunks = []; let size = 0;
    for await (const chunk of request) {
      size += chunk.length;
      if (size > 1024 * 1024) throw new DomainError('body_too_large', '请求不能超过 1 MB', 413);
      chunks.push(chunk);
    }
    try {
      const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error();
      return input;
    } catch { throw new DomainError('invalid_json', '请求必须为 JSON 对象'); }
  };
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      if (request.headers.origin && request.headers.origin !== `http://${request.headers.host}`) throw new DomainError('foreign_origin', '只允许本地同源请求', 403);
      if (request.method === 'GET' && url.pathname === '/health') return json(response, 200, { status: 'ok', service: 'member-3-wms', pid: process.pid, version: '0.2.0', tick: engine.state.tick });
      if (request.method === 'GET' && url.pathname === '/api/snapshot') return json(response, 200, engine.snapshot());
      if (request.method === 'GET' && url.pathname === '/api/replay') return json(response, 200, engine.replay());
      if (request.method === 'GET' && url.pathname === '/api/stats') return json(response, 200, engine.stats());
      if (request.method === 'GET' && url.pathname === '/api/maps') return json(response, 200, engine.state.maps);
      if (request.method === 'GET' && url.pathname === '/api/events/page') return json(response, 200, engine.eventPage(Object.fromEntries(url.searchParams)));
      if (request.method === 'GET' && url.pathname === '/api/stream') {
        response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive', 'X-Accel-Buffering': 'no' });
        response.write(`retry: 1000\nevent: snapshot\ndata: ${JSON.stringify(engine.snapshot())}\n\n`);
        subscribers.add(response);
        request.on('close', () => subscribers.delete(response));
        return;
      }
      if (request.method === 'GET' && url.pathname === '/metrics') {
        const stats = engine.stats();
        response.writeHead(200, { 'Content-Type': 'text/plain; version=0.0.4; charset=utf-8' });
        return response.end(Object.entries({ tick: engine.state.tick, agvs: stats.agvs, tasks_completed: stats.completed, conflicts: stats.conflicts, deadlocks: stats.deadlocks }).map(([name, value]) => `wms_${name} ${value}`).join('\n') + '\n');
      }
      if (request.method === 'GET' && url.pathname.startsWith('/api/')) {
        const collection = url.pathname.split('/')[2];
        if (url.pathname.split('/').length <= 4 && ['skus', 'locations', 'inventory', 'ledger', 'orders', 'tasks', 'agvs', 'events'].includes(collection)) {
          const id = url.pathname.split('/')[3];
          const rows = engine.query(collection, Object.fromEntries(url.searchParams));
          if (!id) return json(response, 200, rows);
          const entity = rows.find(item => item.id === id);
          if (!entity) throw new DomainError('not_found', '记录不存在', 404);
          return json(response, 200, entity);
        }
      }
      if (request.method === 'POST' && url.pathname === '/api/command') {
        const input = await readBody(request);
        const result = engine.command(input); broadcast();
        return json(response, 200, { result, snapshot: engine.snapshot() });
      }
      const resource = url.pathname.match(/^\/api\/(skus|locations|inventory|orders|tasks)(?:\/([^/]+))?(?:\/(allocate|release|cancel|priority))?$/);
      if (resource && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method)) {
        const [, collection, id, operation] = resource;
        const actions = {
          'POST skus': 'sku.create', 'PUT skus/id': 'sku.update', 'PATCH skus/id': 'sku.update', 'DELETE skus/id': 'sku.delete',
          'POST locations': 'location.create', 'PUT locations/id': 'location.update', 'PATCH locations/id': 'location.update',
          'POST inventory': 'inventory.add', 'POST orders': 'order.create', 'POST orders/id/allocate': 'order.allocate',
          'POST orders/id/release': 'order.release', 'POST orders/id/cancel': 'order.cancel',
          'POST tasks/id/cancel': 'task.cancel', 'POST tasks/id/priority': 'task.priority'
        };
        const resourcePath = [collection, id && 'id', operation].filter(Boolean).join('/');
        const action = actions[request.method + ' ' + resourcePath];
        if (!action) throw new DomainError('method_not_allowed', '该资源不支持此操作', 405);
        const body = request.method === 'DELETE' ? {} : await readBody(request);
        const result = engine.command({ ...body, ...(id ? { id } : {}), action }); broadcast();
        return json(response, request.method === 'POST' && !id ? 201 : 200, result);
      }
      if (request.method === 'POST' && url.pathname === '/rpc') {
        const body = await readBody(request);
        if (body.jsonrpc !== '2.0' || typeof body.method !== 'string') return json(response, 200, { jsonrpc: '2.0', id: body.id ?? null, error: { code: -32600, message: '无效 JSON-RPC 请求' } });
        try {
          const result = body.method === 'snapshot' ? engine.snapshot() : engine.command({ ...body.params, action: body.method });
          broadcast();
          if (body.id === undefined) { response.writeHead(204); response.end(); return; }
          return json(response, 200, { jsonrpc: '2.0', id: body.id, result });
        } catch (error) {
          if (body.id === undefined) { response.writeHead(204); response.end(); return; }
          return json(response, 200, { jsonrpc: '2.0', id: body.id, error: { code: error.code === 'unknown_action' ? -32601 : -32602, message: error.message } });
        }
      }
      if (request.method !== 'GET') throw new DomainError('method_not_allowed', '不支持此请求方法', 405);
      const pathname = decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname);
      const filename = resolve(publicRoot, `.${pathname}`);
      if (!filename.startsWith(publicRoot + sep)) throw new DomainError('forbidden', '路径不可访问', 403);
      const content = await readFile(filename).catch(() => { throw new DomainError('not_found', '文件或接口不存在', 404); });
      response.writeHead(200, { 'Content-Type': mime[extname(filename)] ?? 'application/octet-stream', 'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff' });
      response.end(content);
    } catch (error) { json(response, error.status ?? 500, { error: { code: error.code ?? 'internal_error', message: error.status ? error.message : '服务内部错误', details: error.details } }); }
  });
  const timer = setInterval(() => {
    if (!engine.state.running) return;
    try { for (let index = 0; index < engine.state.speed; index++) engine.step(); broadcast(); }
    catch (error) {
      engine.state.running = false;
      engine.event('simulation.error', engine.state.map.id, { message: error.message }); engine.persist(); broadcast();
    }
  }, 1000);
  timer.unref();
  const heartbeat = setInterval(() => { for (const response of subscribers) response.write(': heartbeat\n\n'); }, 15000);
  heartbeat.unref();
  async function close() {
    if (closed) return;
    closed = true;
    clearInterval(timer); clearInterval(heartbeat);
    for (const response of subscribers) response.end();
    engine.close();
    if (server.listening) await new Promise(resolveClose => server.close(resolveClose));
  }
  return { server, engine, broadcast, close };
}

async function ownerIsServing(port, ownerPid) {
  const url = 'http://127.0.0.1:' + port;
  for (let attempt = 0; attempt < 8; attempt++) {
    try {
      const response = await fetch(url + '/health', { redirect: 'error', signal: AbortSignal.timeout(700) });
      if (!response.ok) return false;
      const health = await response.json();
      if (health.status !== 'ok') return false;
      if (health.pid !== undefined) return health.service === 'member-3-wms' && health.pid === ownerPid;
      // Running releases predating the PID field can be identified by their Windows listening socket.
      if (process.platform !== 'win32' || health.version !== '0.2.0') return false;
      const { stdout } = await promisify(execFile)('netstat.exe', ['-ano', '-p', 'tcp'], { windowsHide: true, timeout: 3000 });
      return stdout.split(/\r?\n/).some(line => {
        const [protocol, local, peer, , pid] = line.trim().split(/\s+/);
        return protocol === 'TCP' && local === '127.0.0.1:' + port && peer === '0.0.0.0:0' && Number(pid) === ownerPid;
      });
    } catch {
      // A second click can arrive while the owner is still starting its HTTP listener.
      await new Promise(resolveDelay => setTimeout(resolveDelay, 150));
    }
  }
  return false;
}

async function startLocalServer() {
  const port = Number(process.env.PORT ?? 3033);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT 必须为 1～65535 的整数');
  const url = 'http://127.0.0.1:' + port;
  const filename = resolve(process.env.WMS_DB || resolve(root, 'runtime/wms-member3.db'));
  function showDashboard() {
    if (process.platform === 'win32' && process.argv.includes('--open')) {
      execFile('rundll32.exe', ['url.dll,FileProtocolHandler', url], { windowsHide: true }, error => {
        if (error) console.log('请在浏览器打开 ' + url);
      }).unref();
    }
  }
  let app;
  try { app = createApplication({ filename }); }
  catch (error) {
    if (error.code !== 'WMS_DB_BUSY' || !await ownerIsServing(port, error.ownerPid)) throw error;
    console.log('序仓已在运行，继续使用 ' + url);
    showDashboard();
    return;
  }
  app.server.on('error', async error => {
    console.error('启动失败：' + (error.code === 'EADDRINUSE' ? '端口 ' + port + ' 已被占用' : error.message));
    await app.close(); process.exitCode = 1;
  });
  app.server.listen(port, '127.0.0.1', () => {
    console.log('序仓已启动：' + url + '\n数据文件：' + filename + '\n请保留此窗口，按 Ctrl+C 可停止服务。');
    showDashboard();
  });
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => app.close().then(() => process.exit(0)));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  startLocalServer().catch(error => { console.error('启动失败：' + error.message); process.exitCode = 1; });
}
