import { spawn } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync, existsSync, mkdtempSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { createApplication } from '../src/server.js';
import { Engine } from '../src/engine.js';

const delay = milliseconds => new Promise(resolveDelay => setTimeout(resolveDelay, milliseconds));
const started = performance.now();
const evidenceTag = String(process.env.EVIDENCE_TAG ?? '').replace(/[^a-zA-Z0-9-]/g, '');
const evidencePath = filename => resolve('evidence', filename.replace(/(\.[^.]+)$/, `${evidenceTag ? '-' + evidenceTag : ''}$1`));
const sustainedSeconds = Number(process.env.BROWSER_SUSTAIN_SECONDS ?? 0);
assert.ok(Number.isInteger(sustainedSeconds) && sustainedSeconds >= 0);
const edgePath = process.env.EDGE_PATH ?? 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
assert.ok(existsSync(edgePath), '未找到 Edge，请设置 EDGE_PATH 指向已有 Chromium 浏览器');
mkdirSync(resolve('evidence'), { recursive: true });
const profile = resolve(`runtime/browser-profile-${Date.now()}`);
mkdirSync(profile, { recursive: true });
const databasePath = sustainedSeconds ? resolve(mkdtempSync(resolve('runtime/browser-disk-')), 'wms.db') : null;
const engine = new Engine({ ...(databasePath ? { filename: databasePath } : {}), recording: true });
const app = createApplication({ engine });
await new Promise(resolveReady => app.server.listen(0, '127.0.0.1', resolveReady));
const base = `http://127.0.0.1:${app.server.address().port}`;
const browser = spawn(edgePath, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-background-networking', '--disable-component-update', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--window-size=1600,1000', 'about:blank'], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
let browserError = '';
browser.stderr.on('data', data => { if (browserError.length < 3000) browserError += data.toString(); });
let socket;
const pending = new Map();
let sequence = 0;
const exceptions = [];
try {
  const deadline = Date.now() + 20000;
  const activePort = resolve(profile, 'DevToolsActivePort');
  while (!existsSync(activePort) && Date.now() < deadline) await delay(100);
  assert.ok(existsSync(activePort), `浏览器调试端口未就绪：${browserError}`);
  const port = readFileSync(activePort, 'utf8').split('\n')[0].trim();
  const target = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' }).then(response => response.json());
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolveOpen, reject) => { socket.addEventListener('open', resolveOpen, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  socket.addEventListener('message', message => {
    const payload = JSON.parse(message.data);
    if (payload.id) { const entry = pending.get(payload.id); pending.delete(payload.id); clearTimeout(entry?.timer); payload.error ? entry?.reject(new Error(JSON.stringify(payload.error))) : entry?.resolve(payload.result); }
    else if (payload.method === 'Runtime.exceptionThrown') exceptions.push(payload.params.exceptionDetails);
  });
  const call = (method, params = {}) => new Promise((resolveCall, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP 超时：${method}`)); }, 15000);
    pending.set(id, { resolve: resolveCall, reject, timer }); socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const waitFor = async (expression, message) => {
    for (let attempt = 0; attempt < 100; attempt++) {
      if (await evaluate(expression)) return;
      await delay(100);
    }
    const detail = await evaluate("({command:window.__wmsDiagnostics?.lastCommand, formError:document.querySelector('#form-error')?.textContent, invalid:[...document.querySelectorAll('#editor-form :invalid')].map(node=>({name:node.name,message:node.validationMessage}))})");
    throw new Error(`网页断言超时：${message}；${JSON.stringify(detail)}`);
  };
  const click = selector => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const openForm = async (kind, values, id) => {
    await evaluate(`window.__wmsTest.openForm(${JSON.stringify(kind)}, ${JSON.stringify(id)})`);
    await evaluate(`(() => { const form = document.querySelector('#editor-form'); for (const [name, value] of Object.entries(${JSON.stringify(values)})) { const input = form.elements.namedItem(name); input.value = value; input.dispatchEvent(new Event('input', {bubbles:true})); input.dispatchEvent(new Event('change', {bubbles:true})); } })()`);
  };
  const saveForm = async () => {
    await click('#save-dialog');
    await waitFor("!document.querySelector('#editor-dialog').open", '表单保存并关闭');
    await evaluate('window.__wmsTest.ready()');
  };
  const agvFacts = rows => rows.map(({ id, code, x, y, status, battery }) => ({ id, code, x, y, status, battery }));
  const assertions = {};
  await call('Runtime.enable'); await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: base });
  for (let attempts = 0; attempts < 150; attempts++) {
    if (await evaluate('Boolean(window.__wmsDiagnostics?.snapshotTick !== null && window.__wmsDiagnostics?.frames > 3)')) break;
    await delay(100);
  }
  assert.equal(await evaluate('Boolean(window.__wmsDiagnostics)'), true, '前端诊断尚未初始化');
  await evaluate('window.__wmsTest.ready()');
  await evaluate("window.__wmsTest.navigate('wms')");
  const unsafeText = '<img src=x onerror="window.__unsafeExecuted=true">';
  await openForm('sku', { code: 'UI-SKU', name: unsafeText, spec: '浏览器验收', batch_rule: 'fifo' });
  await saveForm();
  const sku = engine.state.skus.find(item => item.code === 'UI-SKU');
  assert.ok(sku, '实际网页表单应创建商品');
  assert.equal(await evaluate("document.querySelector('#wms-table').textContent.includes(" + JSON.stringify(unsafeText) + ')'), true);
  assert.equal(await evaluate("Boolean(window.__unsafeExecuted || document.querySelector('#wms-table img'))"), false);
  await openForm('sku', { name: '网页新增商品（已编辑）' }, sku.id);
  await saveForm();
  assert.equal(engine.state.skus.find(item => item.id === sku.id).name, '网页新增商品（已编辑）');
  assertions.skuCreateEditAndSafeText = true;

  await openForm('sku', { code: 'UI-SKU', name: '重复编码', batch_rule: 'none' });
  await click('#save-dialog');
  await waitFor("!document.querySelector('#form-error').hidden", '重复编码显示具体错误');
  const expectedValidationError = await evaluate("({form:document.querySelector('#form-error').textContent,banner:document.querySelector('#error-message').textContent,code:window.__wmsDiagnostics.lastCommand.code})");
  assert.match(expectedValidationError.form, /重复|唯一|存在/);
  assert.ok(expectedValidationError.banner);
  await click('#cancel-dialog'); await delay(100); await click('#dismiss-error');
  assertions.duplicateSkuVisibleError = true;

  await openForm('location', { code: 'UI-PICK', zone: '网页验收区', x: 20, y: 14, type: 'pick', capacity: 100 });
  await saveForm();
  const location = engine.state.locations.find(item => item.code === 'UI-PICK');
  assert.ok(location);
  const priorMap = engine.state.map.version;
  await openForm('location', { capacity: 120 }, location.id);
  await saveForm();
  assert.equal(engine.state.locations.find(item => item.id === location.id).capacity, 120);
  assert.notEqual(engine.state.map.version, priorMap);
  assertions.locationCreateEditVersioned = true;
  await openForm('inventory', { sku_id: sku.id, location_id: location.id, qty: 20, batch: 'UI-BATCH', expire_at: '2030-12-31T12:00' });
  await saveForm();
  assert.equal(engine.state.inventory.find(item => item.sku_id === sku.id).qty, 20);
  assert.match(engine.state.inventory.find(item => item.sku_id === sku.id).expire_at, /Z$/);
  assertions.inventoryFormAndUtcDate = true;

  await evaluate("window.__wmsTest.navigate('orders')");
  await openForm('order', { code: 'UI-OUT', requester: '浏览器验收', priority: 7 });
  await click('#add-order-line');
  await evaluate(`(() => { const rows = document.querySelector('#order-line-list').children; const ids = ${JSON.stringify([sku.id, engine.state.skus[0].id])}; [...rows].forEach((row,index) => { row.querySelector('select').value = ids[index]; row.querySelector('input').value = index + 1; }); })()`);
  await saveForm();
  const order = engine.state.orders.find(item => item.code === 'UI-OUT');
  assert.equal(order.lines.length, 2);
  const orderRow = `#orders-table tr[data-id="${order.id}"]`;
  await click(`${orderRow} [data-test="order.allocate"]`);
  await waitFor("window.__wmsDiagnostics.lastCommand?.action === 'order.allocate' && window.__wmsDiagnostics.lastCommand.status === 'success'", '订单预占');
  await evaluate('window.__wmsTest.ready()');
  assert.equal(engine.state.orders.find(item => item.id === order.id).status, 'allocated');
  await click(`${orderRow} [data-test="order.release"]`);
  await waitFor("window.__wmsDiagnostics.lastCommand?.action === 'order.release' && window.__wmsDiagnostics.lastCommand.status === 'success'", '订单释放');
  await evaluate('window.__wmsTest.ready()');
  assert.equal(engine.state.tasks.filter(item => item.order_id === order.id).length, 2);
  assert.equal(engine.state.inventory.find(item => item.sku_id === sku.id).reserved_qty, 1);
  assertions.multiLineOrderAllocateRelease = true;
  const selectors = await evaluate('Array.from(document.querySelectorAll("button")).map(button=>({text:button.textContent.trim(),action:button.dataset.action,id:button.id}))');
  await evaluate("window.__wmsTest.navigate('overview')");
  await click('[data-command="demo.orders"]');
  await waitFor("window.__wmsDiagnostics.lastCommand?.action === 'demo.orders' && window.__wmsDiagnostics.lastCommand.status === 'success'", '演示订单按钮');
  await evaluate('window.__wmsTest.ready()');
  await click('[data-speed="4"]');
  await waitFor("window.__wmsDiagnostics.lastCommand?.action === 'sim.speed' && window.__wmsDiagnostics.lastCommand.status === 'success'", '4倍速按钮');
  await click('[data-command="sim.start"]');
  const fpsSamples = [];
  for (let index = 0; index < 9; index++) { await delay(1000); fpsSamples.push(await evaluate('window.__wmsDiagnostics.fps')); }
  await click('[data-command="sim.pause"]');
  await waitFor("window.__wmsDiagnostics.lastCommand?.action === 'sim.pause' && window.__wmsDiagnostics.lastCommand.status === 'success'", '暂停按钮');
  const pausedTick = engine.state.tick;
  await delay(1100);
  assert.equal(engine.state.tick, pausedTick);
  await click('[data-command="sim.step"]');
  await waitFor(`window.__wmsDiagnostics.snapshotTick === ${pausedTick + 1}`, '单步按钮');
  await evaluate('window.__wmsTest.ready()');
  assert.deepEqual(agvFacts(await evaluate('window.__wmsDiagnostics.renderedAgvs')), agvFacts(engine.state.agvs));
  assertions.liveCoordinatesMatchBackend = true;
  assertions.pauseAndSingleStep = true;

  const taskSample = engine.state.tasks.find(item => item.assigned_agv_id);
  assert.ok(taskSample);
  const taskFilters = { status: taskSample.status, priority: String(taskSample.priority), agv: taskSample.assigned_agv_id };
  const filteredTasks = await evaluate(`window.__wmsTest.filter('tasks', ${JSON.stringify(taskFilters)})`);
  assert.deepEqual(filteredTasks, engine.state.tasks.filter(item => item.status === taskSample.status && item.priority === taskSample.priority && item.assigned_agv_id === taskSample.assigned_agv_id).map(item => item.id));
  const filteredOrders = await evaluate(`window.__wmsTest.filter('orders', ${JSON.stringify({ status: engine.state.orders.find(item => item.id === order.id).status, search: 'UI-OUT' })})`);
  assert.deepEqual(filteredOrders, [order.id]);
  const liveSnapshot = await evaluate('window.__wmsTest.getLiveSnapshot()');
  const eventSample = liveSnapshot.events.find(item => JSON.stringify(item).includes(taskSample.assigned_agv_id));
  assert.ok(eventSample);
  const eventFilters = { agv: taskSample.assigned_agv_id, type: eventSample.type, from: String(eventSample.tick), to: String(eventSample.tick) };
  const filteredEvents = await evaluate(`window.__wmsTest.filter('events', ${JSON.stringify(eventFilters)})`);
  const contains = (value, id) => value === id || (Array.isArray(value) ? value.some(item => contains(item, id)) : value && typeof value === 'object' ? Object.values(value).some(item => contains(item, id)) : false);
  assert.deepEqual(filteredEvents, engine.query('events').filter(item => item.type === eventSample.type && item.tick === eventSample.tick && (item.aggregateId === taskSample.assigned_agv_id || contains(item.payload, taskSample.assigned_agv_id))).reverse().slice(0, 40).map(item => item.id));
  assert.ok(filteredEvents.length);
  assertions.combinedTaskOrderEventFilters = true;

  const fullLog = engine.query('events'); assert.ok(fullLog.length > 200);
  const earlyEvent = fullLog.find(event => event.type === 'task.assigned');
  assert.ok(earlyEvent && !engine.snapshot().events.some(event => event.id === earlyEvent.id));
  const historicalFilters = { agv: earlyEvent.payload.agv_id, task: earlyEvent.aggregateId, type: earlyEvent.type,
    from: '0', to: String(earlyEvent.tick), dateFrom: '2026-11-19T00:00', dateTo: '2026-11-20T23:59', search: '' };
  const oldIds = await evaluate(`window.__wmsTest.filter('events', ${JSON.stringify(historicalFilters)})`);
  assert.deepEqual(oldIds, [earlyEvent.id]);
  assert.ok(await evaluate(`document.querySelector('#events-table').textContent.includes(${JSON.stringify(earlyEvent.aggregateId)})`));
  assertions.completeHistoryBeyond200WithAgvTaskAndDate = true;
  const cleared = { search: '', agv: '', task: '', type: '', from: '', to: '', dateFrom: '', dateTo: '' };
  const firstPage = await evaluate(`window.__wmsTest.filter('events', ${JSON.stringify(cleared)})`);
  assert.deepEqual(firstPage, fullLog.slice().reverse().slice(0, 40).map(event => event.id));
  assert.equal(await evaluate('window.__wmsDiagnostics.eventHistoryTotal'), fullLog.length);
  await click('#events-next');
  await waitFor(`window.__wmsDiagnostics.filteredEvents?.[0] === ${JSON.stringify(fullLog.at(-41).id)}`, '完整日志第二页');
  assert.deepEqual(await evaluate('window.__wmsDiagnostics.filteredEvents'), fullLog.slice().reverse().slice(40, 80).map(event => event.id));
  assertions.completeHistoryServerPagination = true;

  await evaluate(`(() => { document.querySelector('#event-search').value = 'order.created'; document.querySelector('#event-search').dispatchEvent(new Event('input', {bubbles:true})); })()`);
  const expectedSearch = fullLog.filter(event => event.type === 'order.created').reverse().map(event => event.id);
  await waitFor(`JSON.stringify(window.__wmsDiagnostics.filteredEvents) === ${JSON.stringify(JSON.stringify(expectedSearch))}`, '真实输入事件查询历史');
  assertions.historyDomInputQueries = true;
  await evaluate(`(() => { const original = window.fetch; window.__originalFetch = original; window.fetch = async (...args) => { const response = await original(...args); if (String(args[0]).includes('search=task.assigned')) await new Promise(resolve => setTimeout(resolve, 300)); return response; }; })()`);
  const racing = await evaluate(`(async () => { const older = window.__wmsTest.filter('events', {search:'task.assigned'}); await new Promise(resolve=>setTimeout(resolve,30)); await window.__wmsTest.filter('events', {search:'order.created'}); await older; return window.__wmsTest.getDiagnostics().filteredEvents; })()`);
  assert.deepEqual(racing, expectedSearch);
  await evaluate('window.fetch = window.__originalFetch; delete window.__originalFetch;');
  assertions.historyIgnoresStaleFilterResponse = true;

  const replay = engine.replay();
  await evaluate("window.__wmsTest.navigate('replay')");
  await click('#load-replay');
  await waitFor("window.__wmsDiagnostics.mode === 'replay'", '载入回放按钮');
  await evaluate("window.__wmsTest.navigate('overview')");
  const replayChecks = [];
  for (const index of [0, Math.floor(replay.frames.length / 2), replay.frames.length - 1]) {
    const frame = await evaluate(`window.__wmsTest.seekReplay(${index})`);
    assert.deepEqual(agvFacts(frame.agvs), agvFacts(replay.frames[index].agvs));
    assert.deepEqual(frame.events.map(item => item.id), replay.frames[index].events.map(item => item.id));
    assert.deepEqual(agvFacts(await evaluate('window.__wmsDiagnostics.renderedAgvs')), agvFacts(frame.agvs));
    assert.equal(frame.map.version, replay.frames[index].map.version);
    replayChecks.push({ index, tick: frame.tick, agvs: frame.agvs.length, events: frame.events.length });
  }
  assert.equal(await evaluate("[...document.querySelectorAll('[data-command],[data-speed],[data-inject],[data-block],[data-write]')].every(node=>node.disabled)"), true);
  const replayTick = await evaluate('window.__wmsTest.getSnapshot().tick');
  const tickBeforeDisabledClick = engine.state.tick;
  await click('[data-command="sim.step"]');
  assert.equal(engine.state.tick, tickBeforeDisabledClick);
  engine.step(); app.broadcast();
  await waitFor(`window.__wmsDiagnostics.snapshotTick === ${engine.state.tick}`, '回放期间继续接收实时快照');
  assert.equal(await evaluate('window.__wmsTest.getSnapshot().tick'), replayTick);
  await click('#exit-replay');
  await waitFor("window.__wmsDiagnostics.mode === 'live'", '返回实时按钮');
  await evaluate('window.__wmsTest.ready()');
  assert.deepEqual(agvFacts(await evaluate('window.__wmsDiagnostics.renderedAgvs')), agvFacts(engine.state.agvs));
  assertions.replayFramesEventsCoordinatesAndMap = true;
  assertions.replayWriteLockAndLiveIsolation = true;
  assertions.returnToLive = true;
  await waitFor("document.querySelector('#toast').hidden", '操作提示自动消失');
  await evaluate("window.__wmsTest.navigate('analytics')");
  assert.equal(await evaluate("document.querySelector('#utilization-chart').children.length"), engine.state.agvs.length);
  assert.ok(await evaluate("document.querySelector('#throughput-chart').width > 100"));
  const screenshotAnalytics = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  writeFileSync(evidencePath('analytics.png'), Buffer.from(screenshotAnalytics.data, 'base64'));
  assertions.analyticsChartAndFleetRows = true;
  await evaluate("window.__wmsTest.navigate('overview')");
  await evaluate(`window.__wmsTest.selectAgv(${JSON.stringify(taskSample.assigned_agv_id)})`);
  let sustained = null;
  if (sustainedSeconds) {
    const phaseStarted = performance.now(), tickStart = engine.state.tick;
    const initialTasks = engine.state.tasks.length;
    const samples = [];
    let safetyChecks = 0, nextBatchTick = tickStart + 30;
    const originalStep = engine.step.bind(engine);
    engine.step = () => {
      const previous = engine.state.agvs.map(agv => ({ x: agv.x, y: agv.y }));
      const result = originalStep();
      for (let i = 0; i < previous.length; i++) for (let j = i + 1; j < previous.length; j++) {
        const [a, b] = [engine.state.agvs[i], engine.state.agvs[j]];
        assert.ok(a.x !== b.x || a.y !== b.y);
        assert.ok(!(a.x === previous[j].x && a.y === previous[j].y && b.x === previous[i].x && b.y === previous[i].y));
        safetyChecks++;
      }
      return result;
    };
    await evaluate("window.__wmsTest.command('sim.speed', {speed:1})");
    await evaluate("window.__wmsTest.command('sim.start')");
    console.log(`开始浏览器/磁盘持续运行 ${sustainedSeconds} 秒，保持回放记录。`);
    while ((performance.now() - phaseStarted) / 1000 < sustainedSeconds) {
      await delay(1000);
      const diag = await evaluate('window.__wmsTest.getDiagnostics()');
      const sample = { elapsedSeconds: (performance.now() - phaseStarted) / 1000, tick: engine.state.tick,
        fps: diag.fps, completed: engine.stats().completed, databaseBytes: statSync(databasePath).size };
      samples.push(sample);
      assert.ok(sample.fps >= 20, `持续运行FPS不足20：${JSON.stringify(sample)}`);
      assert.ok(engine.state.running, '持续运行中仿真意外停止');
      assert.equal(exceptions.length, 0);
      if (engine.state.tick >= nextBatchTick && sample.elapsedSeconds < sustainedSeconds - 90) {
        await evaluate("window.__wmsTest.command('demo.orders')"); nextBatchTick += 30;
      }
      if (samples.length % 30 === 0) console.log(JSON.stringify({ phase: 'browser-disk', ...sample }));
    }
    await evaluate("window.__wmsTest.command('sim.pause')");
    const diagnostics = await evaluate('window.__wmsTest.ready()');
    assert.deepEqual(agvFacts(diagnostics.renderedAgvs), agvFacts(engine.state.agvs));
    assert.ok(engine.state.tick - tickStart >= sustainedSeconds - 5);
    assert.ok(engine.state.tasks.every(task => task.status === 'completed'));
    assert.deepEqual(engine.store.load(), engine.state);
    sustained = { requestedWallSeconds: sustainedSeconds, elapsedWallSeconds: (performance.now() - phaseStarted) / 1000,
      tickStart, tickEnd: engine.state.tick, agvs: engine.state.agvs.length, tasksBefore: initialTasks, tasksAfter: engine.state.tasks.length,
      completed: engine.stats().completed, failed: 0, unfinished: 0, recording: true, sqliteMode: 'file', databasePath,
      databaseBytes: statSync(databasePath).size, samples, minimumSampleFps: Math.min(...samples.map(sample => sample.fps)),
      safetyChecks, safetyViolations: 0, diskSnapshotMatches: true };
    assertions.sustainedBrowserFileDatabaseAndReplay = true;
  }
  await waitFor("document.querySelector('#toast').hidden", '操作提示自动消失');
  const diagnostics = await evaluate('JSON.parse(JSON.stringify(window.__wmsDiagnostics))');
  const canvas = await evaluate('Array.from(document.querySelectorAll("canvas")).map(canvas=>({width:canvas.width,height:canvas.height}))');
  assert.ok(canvas.some(item => item.width > 100 && item.height > 100));
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  const liveTick = engine.state.tick;
  assert.ok(liveTick >= 16, '真实定时器应推动仿真');
  const fps = diagnostics.fps;
  assert.ok(fps >= 20, `前端实测FPS未达20：${fps}`);
  assert.ok(fpsSamples.every(value => value >= 20), `运行期逐秒采样FPS：${fpsSamples}`);
  assert.ok(diagnostics.errors.every(error => error.context === '保存失败' && error.code === expectedValidationError.code), '只允许主动触发的重复编码校验提示');
  assert.equal(diagnostics.errorCount, 1, '应只有一次预期的重复编码提示');
  const screenshot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  writeFileSync(evidencePath('dashboard.png'), Buffer.from(screenshot.data, 'base64'));
  const mobile = await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await delay(500);
  const responsive = await evaluate('({viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,bodyWidth:document.body.scrollWidth})');
  assert.equal(responsive.scrollWidth, 390);
  assert.equal(responsive.bodyWidth, 390);
  assert.equal(await evaluate("[...document.querySelectorAll('.kpi-value')].every(node => node.scrollWidth <= node.clientWidth)"), true, '手机指标数值和单位不能被卡片裁切');
  const screenshotMobile = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  writeFileSync(evidencePath('dashboard-mobile.png'), Buffer.from(screenshotMobile.data, 'base64'));
  await evaluate("document.querySelector('#map-panel').scrollIntoView({block:'start'})");
  await delay(300);
  const screenshotMobileMap = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  writeFileSync(evidencePath('dashboard-mobile-map.png'), Buffer.from(screenshotMobileMap.data, 'base64'));
  const report = { measuredAt: new Date().toISOString(), elapsedMs: performance.now() - started, browser: await call('Browser.getVersion'), viewport: { width: 1600, height: 1000 }, liveTick,
    diagnostics, canvas, responsive, exceptions, buttons: selectors, fpsSamples, minimumSampleFps: Math.min(...fpsSamples), expectedValidationError, replayChecks, sustained,
    historicalQuery: { total: fullLog.length, snapshotEvents: liveSnapshot.events.length, earlyEventId: earlyEvent.id, matchedHistoricalIds: oldIds, firstPage, secondPage: fullLog.slice().reverse().slice(40, 80).map(event => event.id) },
    assertions: { ...assertions, pageLoaded: true, canvasRendered: true, timerAdvanced: true, fpsAtLeast20: true, noUncaughtExceptions: true, mobileNoHorizontalOverflow: true },
    screenshots: ['dashboard.png', 'dashboard-mobile.png', 'dashboard-mobile-map.png', 'analytics.png'].map(evidencePath), limitation: 'Windows Edge headless受控环境的短时间实测，不能代表全部用户设备和30分钟帧率稳定性。' };
  writeFileSync(evidencePath('browser-results.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ measuredAt: report.measuredAt, elapsedMs: report.elapsedMs, assertions: report.assertions,
    historicalQuery: { total: fullLog.length, earlyEventId: earlyEvent.id, matchedHistoricalIds: oldIds },
    sustained: sustained && { ...sustained, samples: `${sustained.samples.length} samples in report` },
    minimumSampleFps: report.minimumSampleFps, report: evidencePath('browser-results.json') }, null, 2));
  await call('Browser.close').catch(() => {});
} finally {
  for (const entry of pending.values()) { clearTimeout(entry.timer); entry.reject(new Error('浏览器测试结束')); }
  pending.clear();
  socket?.close();
  browser.kill();
  await app.close();
}
