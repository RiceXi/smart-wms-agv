(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const array = value => Array.isArray(value) ? value : [];
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const text = value => value === null || value === undefined || value === '' ? '—' : String(value);
  const fmt = value => number(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '—';
  const short = value => value ? String(value).slice(0, 8) : '—';
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const names = {
    draft: '草稿', allocated: '已预占', released: '已释放', completed: '已完成', cancelled: '已取消', failed: '失败',
    pending: '待处理', assigned: '已指派', running: '执行中', idle: '空闲', to_pickup: '前往取货', loading: '装载中',
    to_dropoff: '前往放货', unloading: '卸载中', charging: '充电中', fault: '故障', offline: '离线',
    floor: '通道', shelf: '货架', pick: '拣选点', drop: '放货点', charger: '充电桩', blocked: '封路格',
    fifo: '先进先出', fefo: '先到期先出', none: '无批次规则', move: '移动', wait: '等待'
  };
  const orderStates = ['draft', 'allocated', 'released', 'completed', 'cancelled', 'failed'];
  const taskStates = ['pending', 'assigned', 'running', 'completed', 'cancelled', 'failed'];
  const agvStates = ['idle', 'to_pickup', 'loading', 'to_dropoff', 'unloading', 'charging', 'fault', 'offline'];
  const colors = ['#0a9d8a', '#5286d4', '#c78b39', '#9175c5', '#c96580', '#4d9db6', '#7c9e4a', '#c8784b'];
  const pages = {
    overview: ['仓库运行总览', '让每一次搬运，都有迹可循。', '运行总览'],
    wms: ['仓储主数据', '从商品、库位到库存，管理每一份真实资产。', '仓储管理'],
    orders: ['出库订单', '预占、释放、执行；追踪每一张出库单。', '出库订单'],
    tasks: ['任务调度中心', '连接业务订单与设备执行的每一个环节。', '任务调度'],
    events: ['事件追踪', '以 tick 为线索，定位每一次状态变化。', '事件追踪'],
    analytics: ['运行分析', '以实际运行数据，观察效率与拥堵。', '运行分析'],
    replay: ['历史回放', '回到记录中的某一刻，还原真实运行状态。', '历史回放']
  };
  const state = {
    live: null, display: null, page: 'overview', wmsTab: 'skus', selectedAgv: '', busy: false,
    connected: false, revision: 0, replay: false, replayFrames: [], replayIndex: 0, playing: false,
    replayLast: 0, trend: [], eventPage: 0, dirty: true, lastUi: 0, dialog: null, toastTimer: 0,
    stream: null, mapGeometry: null, hitTargets: [], lastReceivedAt: null, refreshing: null,
    eventResult: null, eventTypes: [], eventRequest: null, eventRequestId: 0, eventTimer: 0, eventLoading: false
  };
  const diagnostics = window.__wmsDiagnostics = {
    fps: 0, frames: 0, snapshotTick: null, renderedTick: null, errors: [], errorCount: 0,
    mode: 'live', connection: 'connecting', snapshots: 0, sseSnapshots: 0, renderedAgvs: [],
    selectedAgv: null, lastCommand: null, pendingCommands: 0, replayIndex: null, replayFrames: 0,
    canvas: { width: 0, height: 0, dpr: 1 }, mapDraws: 0, page: 'overview'
  };
  function el(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = text(value);
    return node;
  }
  function set(id, value) { $(id).textContent = text(value); }
  function badge(value) {
    const tone = ['completed', 'idle', 'recovered'].includes(value) ? 'good'
      : ['fault', 'failed', 'blocked'].includes(value) ? 'bad'
        : ['pending', 'loading', 'unloading', 'charging'].includes(value) ? 'warn'
          : ['cancelled', 'offline', 'draft'].includes(value) ? 'gray' : 'blue';
    return el('span', `badge ${tone}`, names[value] || value);
  }
  function empty(target, message = '暂无数据', hint = '等待真实业务数据，或调整筛选条件。') {
    const box = el('div', 'empty'); box.append(el('strong', '', message), el('span', '', hint));
    target.replaceChildren(box);
  }
  function toast(message) {
    clearTimeout(state.toastTimer); set('toast', message); $('toast').hidden = false;
    state.toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3500);
  }
  function report(error, context = '请求失败') {
    const message = error instanceof Error ? error.message : String(error);
    const entry = { at: new Date().toISOString(), context, message, code: error?.code || null };
    diagnostics.errors.push(entry); diagnostics.errorCount++;
    if (diagnostics.errors.length > 80) diagnostics.errors.shift();
    set('error-title', context); set('error-message', `${error?.code ? `[${error.code}] ` : ''}${message}`);
    $('error-banner').hidden = false;
    return entry;
  }
  window.addEventListener('error', event => report(event.error || event.message, '页面运行错误'));
  window.addEventListener('unhandledrejection', event => report(event.reason, '异步操作失败'));
  function safely(job, context) {
    return Promise.resolve().then(job).catch(error => { report(error, context); return null; });
  }
  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, { cache: 'no-store', ...options, signal: controller.signal });
      let body;
      try { body = await response.json(); } catch { throw new Error(`服务返回非 JSON 数据（HTTP ${response.status}）`); }
      if (!response.ok || body?.error) {
        const error = new Error(body?.error?.message || `请求失败（HTTP ${response.status}）`);
        error.code = body?.error?.code || `HTTP_${response.status}`;
        error.details = body?.error?.details; throw error;
      }
      return body;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超过 15 秒。写入可能已生效，请刷新快照核对后再操作。');
      if (error instanceof TypeError) throw new Error('无法连接本地服务，请检查服务是否启动。');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function normalized(snapshot, fallbackMap = null) {
    if (!snapshot || !Number.isInteger(snapshot.tick) || snapshot.tick < 0 || !Array.isArray(snapshot.agvs)) {
      throw new Error('快照格式不合法：需要非负整数 tick 和 agvs 数组。');
    }
    const map = snapshot.map || fallbackMap;
    if (!map || !Number.isInteger(map.width) || !Number.isInteger(map.height) || map.width <= 0 || map.height <= 0) {
      throw new Error('快照缺少有效的地图尺寸。');
    }
    return { ...snapshot, map, skus: array(snapshot.skus), locations: array(snapshot.locations), inventory: array(snapshot.inventory),
      orders: array(snapshot.orders), tasks: array(snapshot.tasks), events: array(snapshot.events), stats: snapshot.stats || {}, metrics: snapshot.metrics || {} };
  }
  function acceptSnapshot(raw, source) {
    const snapshot = normalized(raw);
    if (state.live && (snapshot.tick < state.live.tick || snapshot.seed !== state.live.seed)) resetEventHistory();
    state.live = snapshot; state.revision++; state.lastReceivedAt = new Date();
    diagnostics.snapshotTick = snapshot.tick; diagnostics.snapshots++;
    if (source === 'sse') diagnostics.sseSnapshots++;
    const previous = state.trend.at(-1);
    if (previous && (snapshot.tick < previous.tick || snapshot.seed !== previous.seed)) state.trend = [];
    if (number(snapshot.stats.throughput)) {
      const point = { tick: snapshot.tick, throughput: snapshot.stats.throughput, seed: snapshot.seed };
      if (state.trend.at(-1)?.tick === snapshot.tick) state.trend[state.trend.length - 1] = point;
      else state.trend.push(point);
      if (state.trend.length > 180) state.trend.shift();
    }
    if (!state.replay) { state.display = snapshot; state.dirty = true; }
    queueEventRefresh();
  }
  async function refreshSnapshot() {
    if (state.refreshing) return state.refreshing;
    const revision = state.revision;
    state.refreshing = request('/api/snapshot').then(snapshot => {
      if (revision === state.revision) acceptSnapshot(snapshot, 'get');
      return state.live;
    }).finally(() => { state.refreshing = null; });
    return state.refreshing;
  }
  function connection(status) {
    diagnostics.connection = status;
    state.connected = status === 'live';
    const labels = { live: '实时连接正常', connecting: '正在连接', retrying: '连接中断 · 自动重连', unsupported: '快照轮询模式' };
    $('connection').replaceChildren(el('i', `dot ${state.connected ? 'good' : 'warn'}`), document.createTextNode(labels[status]));
    set('side-status', labels[status]); $('side-dot').className = `dot ${state.connected ? 'good' : 'warn'}`;
  }
  function connect() {
    if (!window.EventSource) { connection('unsupported'); return; }
    const stream = state.stream = new EventSource('/api/stream');
    stream.addEventListener('open', () => {
      connection('live');
      safely(refreshSnapshot, '重连后读取快照失败');
    });
    stream.addEventListener('snapshot', event => {
      try { acceptSnapshot(JSON.parse(event.data), 'sse'); connection('live'); }
      catch (error) { report(error, '实时快照解析失败'); }
    });
    stream.addEventListener('error', () => connection('retrying'));
  }
  function syncLocks() {
    const locked = state.busy || state.replay || !state.live;
    document.querySelectorAll('[data-command],[data-speed],[data-inject],[data-block],[data-write]').forEach(node => {
      node.disabled = locked || node.dataset.unavailable === 'true';
    });
    $('reset-sim').disabled = locked;
    $('save-dialog').disabled = locked;
  }
  async function command(action, payload = {}) {
    if (state.replay) throw new Error('历史回放中无法写入，请先返回实时工作区。');
    if (state.busy) throw new Error('上一条命令仍在处理，请稍后操作。');
    state.busy = true; diagnostics.pendingCommands = 1;
    diagnostics.lastCommand = { action, status: 'pending', startedAt: new Date().toISOString() };
    syncLocks();
    try {
      const body = await request('/api/command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...payload, action }) });
      if (action === 'sim.reset') resetEventHistory();
      if (!state.live || action === 'sim.reset' || body.snapshot?.tick >= state.live.tick) acceptSnapshot(body.snapshot, 'command');
      diagnostics.lastCommand = { action, status: 'success', result: body.result, tick: body.snapshot.tick, finishedAt: new Date().toISOString() };
      $('command-evidence').hidden = false;
      set('command-evidence-title', `${action} · 服务端返回结果 · tick ${body.snapshot.tick}`);
      set('command-evidence-result', JSON.stringify(body.result ?? null, null, 2));
      if (action === 'demo.conflict' || action === 'demo.deadlock') $('command-evidence').open = true;
      toast(action === 'demo.deadlock' ? '等待图已人工注入；恢复数将在真实车辆完成回退后增加。'
        : action === 'demo.conflict' ? '在线两车预约竞争已执行，真实路径见地图，竞争与绕行结果见下方证据。' : '操作成功 · 已收到服务端快照');
      return body;
    } catch (error) {
      diagnostics.lastCommand = { action, status: 'error', code: error.code || null, message: error.message };
      throw error;
    } finally { state.busy = false; diagnostics.pendingCommands = 0; syncLocks(); }
  }
  function setOptions(select, choices, placeholder) {
    const prior = select.value;
    const choicesWithPlaceholder = placeholder === undefined ? choices : [['', placeholder], ...choices];
    const key = JSON.stringify(choicesWithPlaceholder);
    if (select.dataset.optionsKey === key) return;
    if (document.activeElement === select) return;
    select.dataset.optionsKey = key;
    select.replaceChildren(...choicesWithPlaceholder.map(([value, label]) => {
      const option = el('option', '', label); option.value = String(value); return option;
    }));
    if ([...select.options].some(option => option.value === prior)) select.value = prior;
  }
  function selectAgv(id) {
    state.selectedAgv = String(id || ''); diagnostics.selectedAgv = state.selectedAgv || null;
    $('map-agv').value = state.selectedAgv; state.dirty = true;
  }
  function navigate(page) {
    if (!pages[page]) page = 'overview';
    state.page = page; diagnostics.page = page;
    document.querySelectorAll('[data-view]').forEach(node => { node.hidden = node.dataset.view !== page; });
    document.querySelectorAll('nav [data-page]').forEach(node => {
      node.classList.toggle('active', node.dataset.page === page);
      if (node.dataset.page === page) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
    });
    set('page-title', pages[page][0]); set('page-description', pages[page][1]); set('page-breadcrumb', pages[page][2]);
    state.dirty = true;
    queueEventRefresh();
  }
  function locationById(id, snapshot = state.display) { return snapshot?.locations.find(item => item.id === id); }
  function agvById(id, snapshot = state.display) { return snapshot?.agvs.find(item => item.id === id); }
  function skuLabel(id) { const sku = state.display?.skus.find(item => item.id === id); return sku ? `${sku.code} · ${sku.name}` : short(id); }
  function locationLabel(id) { return locationById(id)?.code || short(id); }
  function agvLabel(id) { return agvById(id)?.code || short(id); }
  function orderLabel(id) { return state.display?.orders.find(item => item.id === id)?.code || short(id); }
  function renderKpis(snapshot) {
    const stats = snapshot.stats;
    const values = [
      ['出库订单', stats.orders, '单', `已完成任务 ${fmt(stats.completed)}`, '▤'],
      ['调度任务', stats.tasks, '项', `已取消 ${fmt(stats.cancelled)}`, '⇄'],
      ['在线设备', snapshot.agvs.filter(item => !['offline', 'fault'].includes(item.status)).length, '台', `车队总数 ${fmt(stats.agvs)}`, '▣'],
      ['累计完成', stats.completed, '项', `恢复次数 ${fmt(stats.recoveries)}`, '✓'],
      ['吞吐量', stats.throughput, '任务/小时', '服务端累计平均值', '↗'],
      ['平均任务耗时', stats.averageTaskTicks, 'tick', '以实际完成任务统计', '◷']
    ];
    $('kpis').replaceChildren(...values.map(([label, value, unit, note, icon], index) => {
      const card = el('article', `kpi ${index === 3 || index === 4 ? 'accent' : ''}`);
      card.dataset.test = `kpi-${['orders', 'tasks', 'agvs', 'completed', 'throughput', 'average'][index]}`;
      const title = el('div', 'kpi-label', label); title.append(el('span', 'kpi-icon', icon));
      const val = el('div', 'kpi-value', fmt(value)); val.append(el('em', '', unit));
      card.append(title, val, el('div', 'kpi-note', note)); return card;
    }));
  }
  function renderShared(snapshot) {
    set('tick', snapshot.tick);
    set('sim-state', state.replay ? '历史快照' : snapshot.running ? '仿真运行中' : '仿真已暂停');
    $('sim-dot').className = `dot ${snapshot.running && !state.replay ? 'good' : 'warn'}`;
    set('start-sim', snapshot.tick > 0 ? '▶ 继续' : '▶ 启动');
    document.querySelectorAll('[data-speed]').forEach(node => node.setAttribute('aria-pressed', String(Number(node.dataset.speed) === snapshot.speed)));
    if (document.activeElement !== $('seed') && snapshot.seed !== undefined) $('seed').value = snapshot.seed;
    set('snapshot-time', state.replay ? `历史帧 · tick ${snapshot.tick}` : `最近快照 ${state.lastReceivedAt?.toLocaleTimeString('zh-CN') || '—'}`);
    $('mode-label').replaceChildren(el('i', 'dot'), document.createTextNode(state.replay ? '历史回放工作区' : '实时工作区'));
    $('replay-banner').hidden = !state.replay;
    set('map-version', `v${snapshot.map.version ?? '—'}`);
    set('map-dimensions', `${snapshot.map.width} × ${snapshot.map.height}  ·  X → / Y ↓`);
    set('fleet-count', snapshot.agvs.length);
    const agvs = snapshot.agvs.map(item => [item.id, item.code || short(item.id)]);
    setOptions($('map-agv'), agvs, '全部车辆');
    setOptions($('inject-agv'), agvs);
    setOptions($('task-agv'), [['__none', '未指派'], ...agvs], '全部车辆');
    setOptions($('event-agv'), agvs, '全部车辆');
    setOptions($('event-task'), snapshot.tasks.map(item => [item.id, item.code || short(item.id)]), '全部任务');
    const eventTypes = state.replay ? [...new Set(snapshot.events.map(item => item.type))].sort()
      : [...new Set([...state.eventTypes, ...snapshot.events.map(item => item.type)])].sort();
    setOptions($('event-type'), eventTypes.map(item => [item, item]), '全部类型');
    const cells = new Map(array(snapshot.map.closable).map(item => [`${item.x},${item.y}`, item]));
    array(snapshot.map.nodes).filter(item => item.type === 'blocked').forEach(item => cells.set(`${item.x},${item.y}`, item));
    setOptions($('block-cell'), [...cells].map(([key]) => [key, `(${key})${array(snapshot.map.nodes).some(item => `${item.x},${item.y}` === key && item.type === 'blocked') ? ' · 已封' : ''}`]));
    if (state.selectedAgv && !agvById(state.selectedAgv)) selectAgv('');
    $('map-agv').value = state.selectedAgv;
    renderKpis(snapshot); syncLocks();
  }
  function renderFleet(snapshot) {
    const rows = snapshot.agvs.filter(item => !$('fleet-status').value || item.status === $('fleet-status').value);
    if (!rows.length) empty($('fleet-list'), '没有匹配车辆');
    else $('fleet-list').replaceChildren(...rows.map(agv => {
      const button = el('button', `fleet-row ${agv.status === 'fault' ? 'fault' : ''} ${agv.id === state.selectedAgv ? 'selected' : ''}`);
      button.type = 'button'; button.dataset.test = 'fleet-agv'; button.dataset.agvId = agv.id;
      button.setAttribute('aria-pressed', String(agv.id === state.selectedAgv));
      const main = el('span', 'fleet-row-main'); main.append(el('strong', '', agv.code || short(agv.id)), el('small', '', `(${agv.x}, ${agv.y}) · ${agv.active_task_id ? `任务 ${short(agv.active_task_id)}` : '无当前任务'}`));
      const meta = el('span', 'fleet-row-meta'); meta.append(badge(agv.status));
      const battery = el('span', `battery ${agv.battery < 20 ? 'low' : ''}`);
      const track = el('span', 'battery-track'), fill = el('i'); fill.style.width = `${clamp(agv.battery || 0, 0, 100)}%`; track.append(fill);
      battery.append(track, document.createTextNode(`${fmt(agv.battery)}%`)); meta.append(battery);
      button.append(el('span', 'agv-icon', '▣'), main, meta); button.addEventListener('click', () => selectAgv(agv.id)); return button;
    }));
    const target = $('agv-detail'), agv = agvById(state.selectedAgv);
    if (!agv) { empty(target, '选择一台 AGV', '点击地图或车队列表查看设备详情。'); return; }
    const head = el('div', 'detail-heading'); head.append(el('strong', '', agv.code || short(agv.id)), badge(agv.status));
    const details = el('div', 'detail-grid');
    [['位置', `(${agv.x}, ${agv.y})`], ['电量', `${fmt(agv.battery)}%`], ['当前任务', short(agv.active_task_id)], ['未来路径', `${array(agv.path).length} 个点`]].forEach(([key, val]) => {
      const node = el('div'); node.append(el('span', '', key), document.createTextNode(val)); details.append(node);
    });
    const link = el('button', 'button small ghost', '查看当前任务 ↗'); link.disabled = !agv.active_task_id;
    link.addEventListener('click', () => showDetails('task', snapshot.tasks.find(task => task.id === agv.active_task_id)));
    target.replaceChildren(head, details, link);
  }
  function recentEvents(target, events, limit = 5) {
    const rows = array(events).slice(-limit).reverse();
    if (!rows.length) { empty(target, '暂无事件', '事件将由实际业务与仿真操作产生。'); return; }
    target.replaceChildren(...rows.map(event => {
      const row = el('div', 'recent-event'); row.dataset.test = 'recent-event';
      const body = el('div', 'recent-event-content');
      body.append(el('strong', '', event.type), el('small', '', `${short(event.aggregateId)} · ${JSON.stringify(event.payload || {})}`));
      row.append(el('i', `dot ${/fault|deadlock|conflict|failed|error/.test(event.type) ? 'bad' : ''}`), body, el('time', '', `T ${event.tick}`));
      return row;
    }));
  }

  const canvas = $('warehouse-map');
  const ctx = canvas.getContext('2d');
  const floorCanvas = document.createElement('canvas');
  let floorKey = '', floorGeometry = null;
  function canvasSize(target) {
    const rect = target.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return null;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.round(rect.width * dpr), height = Math.round(rect.height * dpr);
    if (target.width !== width || target.height !== height) { target.width = width; target.height = height; }
    return { width: rect.width, height: rect.height, dpr };
  }
  function roundRect(context, x, y, w, h, radius, fill, stroke) {
    if (w <= 0 || h <= 0) return;
    context.beginPath(); context.roundRect(x, y, w, h, Math.max(0, Math.min(radius, w / 2, h / 2)));
    if (fill) { context.fillStyle = fill; context.fill(); }
    if (stroke) { context.strokeStyle = stroke; context.stroke(); }
  }
  function cellCenter(x, y, geometry) { return { x: geometry.ox + (x + .5) * geometry.cell, y: geometry.oy + (y + .5) * geometry.cell }; }
  function drawFloor(snapshot, size) {
    const map = snapshot.map;
    const key = `${map.id}|${map.version}|${map.width}|${map.height}|${size.width}|${size.height}|${size.dpr}|${$('show-grid').checked}`;
    if (key === floorKey) return floorGeometry;
    floorKey = key;
    floorCanvas.width = canvas.width; floorCanvas.height = canvas.height;
    const context = floorCanvas.getContext('2d'); context.setTransform(size.dpr, 0, 0, size.dpr, 0, 0);
    const pad = 28;
    const cell = Math.min((size.width - pad * 2) / map.width, (size.height - pad * 2) / map.height);
    const geometry = { cell, ox: (size.width - cell * map.width) / 2, oy: (size.height - cell * map.height) / 2 };
    floorGeometry = geometry;
    context.fillStyle = '#f8fafc'; context.fillRect(0, 0, size.width, size.height);
    const { ox, oy } = geometry;
    context.fillStyle = '#f1f5f9'; context.fillRect(ox, oy, cell * map.width, cell * map.height);
    const palette = { floor: '#ffffff', shelf: '#c5d1dc', pick: '#deebfc', drop: '#fff0d9', charger: '#d9f1e8', blocked: '#f9dfe2' };
    array(map.nodes).forEach(node => {
      const x = ox + node.x * cell, y = oy + node.y * cell;
      const fill = palette[node.type] || (node.walkable === false ? '#d1dbe4' : '#ffffff');
      roundRect(context, x + 1, y + 1, cell - 2, cell - 2, cell * .10, fill);
      if (node.type === 'shelf') {
        context.strokeStyle = '#aebfcd'; context.lineWidth = .7;
        context.beginPath(); context.moveTo(x + cell * .23, y + cell * .31); context.lineTo(x + cell * .77, y + cell * .31);
        context.moveTo(x + cell * .23, y + cell * .66); context.lineTo(x + cell * .77, y + cell * .66); context.stroke();
      } else if (['pick', 'drop', 'charger', 'blocked'].includes(node.type)) {
        context.fillStyle = { pick: '#7399cc', drop: '#c6964d', charger: '#4b9e88', blocked: '#cd7981' }[node.type];
        context.font = `600 ${Math.max(8, Math.min(15, cell * .44))}px Segoe UI`;
        context.textAlign = 'center'; context.textBaseline = 'middle';
        context.fillText({ pick: 'P', drop: 'D', charger: 'ϟ', blocked: '×' }[node.type], x + cell / 2, y + cell / 2);
      }
    });
    if ($('show-grid').checked) {
      context.beginPath(); context.lineWidth = .5; context.strokeStyle = '#dfe7ef';
      for (let x = 0; x <= map.width; x++) { context.moveTo(ox + x * cell, oy); context.lineTo(ox + x * cell, oy + map.height * cell); }
      for (let y = 0; y <= map.height; y++) { context.moveTo(ox, oy + y * cell); context.lineTo(ox + map.width * cell, oy + y * cell); }
      context.stroke();
    }
    context.textBaseline = 'middle'; context.textAlign = 'center'; context.font = '9px Segoe UI'; context.fillStyle = '#a6b6c3';
    for (let x = 0; x < map.width; x += Math.max(1, Math.ceil(18 / cell))) context.fillText(String(x), ox + (x + .5) * cell, oy - 11);
    for (let y = 0; y < map.height; y += Math.max(1, Math.ceil(14 / cell))) context.fillText(String(y), ox - 13, oy + (y + .5) * cell);
    return geometry;
  }
  function drawLine(points, geometry, color, width, dash = []) {
    if (points.length < 2) return;
    ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.setLineDash(dash);
    points.forEach((point, index) => {
      if (!number(point.x) || !number(point.y)) return;
      const pos = cellCenter(point.x, point.y, geometry);
      if (index === 0) ctx.moveTo(pos.x, pos.y); else ctx.lineTo(pos.x, pos.y);
    });
    ctx.stroke(); ctx.setLineDash([]);
  }
  function drawMap() {
    if (state.page !== 'overview') return;
    const size = canvasSize(canvas); if (!size || !ctx) return;
    ctx.setTransform(size.dpr, 0, 0, size.dpr, 0, 0);
    diagnostics.canvas = { width: size.width, height: size.height, dpr: size.dpr };
    if (!state.display) { ctx.clearRect(0, 0, size.width, size.height); return; }
    const snapshot = state.display;
    $('map-empty').hidden = true;
    const geometry = drawFloor(snapshot, size); state.mapGeometry = geometry;
    ctx.drawImage(floorCanvas, 0, 0, size.width, size.height);
    ctx.save(); ctx.beginPath(); ctx.rect(geometry.ox, geometry.oy, snapshot.map.width * geometry.cell, snapshot.map.height * geometry.cell); ctx.clip();
    snapshot.agvs.forEach((agv, index) => {
      if (state.selectedAgv && state.selectedAgv !== agv.id) return;
      const color = colors[index % colors.length];
      if ($('show-history').checked) {
        drawLine(array(agv.history).filter(point => point.tick <= snapshot.tick).slice(-100), geometry, `${color}55`, 2);
      }
      if ($('show-paths').checked) {
        const future = array(agv.path).filter(point => point.tick >= snapshot.tick);
        drawLine([{ x: agv.x, y: agv.y }, ...future], geometry, `${color}b0`, Math.max(1.5, geometry.cell * .07), [4, 4]);
        future.filter(point => point.action === 'wait').forEach(point => {
          const pos = cellCenter(point.x, point.y, geometry); ctx.strokeStyle = color; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.arc(pos.x, pos.y, geometry.cell * .13, 0, Math.PI * 2); ctx.stroke();
        });
      }
      if ($('show-links').checked && agv.active_task_id) {
        const task = snapshot.tasks.find(item => item.id === agv.active_task_id);
        const pickup = locationById(task?.pickup_location_id), dropoff = locationById(task?.drop_location_id);
        if (pickup && dropoff) drawLine([{ x: agv.x, y: agv.y }, pickup, dropoff], geometry, `${color}70`, 1, [2, 6]);
      }
    });
    state.hitTargets = [];
    snapshot.agvs.forEach((agv, index) => {
      if (!number(agv.x) || !number(agv.y)) return;
      const pos = cellCenter(agv.x, agv.y, geometry), cell = geometry.cell;
      const selected = agv.id === state.selectedAgv;
      const muted = state.selectedAgv && !selected;
      const color = agv.status === 'fault' ? '#d55c67' : agv.status === 'offline' ? '#93a3b0' : colors[index % colors.length];
      ctx.globalAlpha = muted ? .37 : 1;
      if (selected) {
        ctx.beginPath(); ctx.arc(pos.x, pos.y, cell * .50, 0, Math.PI * 2); ctx.strokeStyle = `${color}70`; ctx.lineWidth = 1.6; ctx.stroke();
      }
      roundRect(ctx, pos.x - cell * .32, pos.y - cell * .30, cell * .64, cell * .60, cell * .16, color, '#ffffff');
      roundRect(ctx, pos.x - cell * .17, pos.y - cell * .17, cell * .34, cell * .16, cell * .04, '#ffffffaa');
      ctx.fillStyle = '#ffffff'; ctx.fillRect(pos.x - cell * .10, pos.y + cell * .14, cell * .20, Math.max(1, cell * .035));
      if (cell >= 17 && !muted) {
        ctx.font = `600 ${Math.max(7, Math.min(10, cell * .28))}px Segoe UI`; ctx.fillStyle = '#476477'; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        ctx.fillText(String(agv.code || short(agv.id)), pos.x, pos.y + cell * .35);
      }
      ctx.globalAlpha = 1; state.hitTargets.push({ id: agv.id, x: pos.x, y: pos.y, radius: Math.max(10, cell * .45) });
    });
    ctx.restore();
    diagnostics.renderedTick = snapshot.tick; diagnostics.mapDraws++;
    diagnostics.renderedAgvs = snapshot.agvs.map(({ id, code, x, y, status, battery }) => ({ id, code, x, y, status, battery }));
    diagnostics.renderedMapVersion = snapshot.map.version;
  }
  function hitAt(event) {
    const rect = canvas.getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top;
    return state.hitTargets.findLast(item => Math.hypot(x - item.x, y - item.y) <= item.radius);
  }
  canvas.addEventListener('click', event => { const hit = hitAt(event); if (hit) selectAgv(hit.id); });
  canvas.addEventListener('mousemove', event => {
    const hit = hitAt(event), agv = hit && agvById(hit.id), tooltip = $('map-hover');
    canvas.style.cursor = agv ? 'pointer' : 'default'; tooltip.hidden = !agv;
    if (!agv) return;
    tooltip.textContent = `${agv.code || agv.id} · ${names[agv.status] || agv.status}\n坐标 (${agv.x}, ${agv.y}) · 电量 ${fmt(agv.battery)}%\n任务 ${short(agv.active_task_id)}`;
    tooltip.style.left = `${Math.max(4, Math.min(hit.x + 14, canvas.clientWidth - 210))}px`;
    tooltip.style.top = `${Math.max(4, Math.min(hit.y + 14, canvas.clientHeight - 80))}px`;
  });
  canvas.addEventListener('mouseleave', () => { $('map-hover').hidden = true; });

  function actionButton(label, action, payload, allowed = true, danger = false) {
    const button = el('button', `button small ${danger ? 'danger' : ''}`, label);
    button.type = 'button'; button.dataset.write = 'true'; button.dataset.test = action;
    button.dataset.unavailable = String(!allowed); button.disabled = !allowed || state.busy || state.replay;
    button.addEventListener('click', () => safely(async () => {
      if (danger && !window.confirm(`确定${label}？此操作将改变实际业务数据。`)) return;
      await command(action, payload);
    }, `${label}失败`));
    return button;
  }
  function table(target, columns, rows, renderRow) {
    if (!rows.length) { empty(target, '暂无匹配记录', '可新增业务数据，或调整当前组合筛选。'); return; }
    const grid = el('table'), head = el('thead'), header = el('tr'), body = el('tbody');
    columns.forEach(label => header.append(el('th', '', label))); head.append(header);
    rows.forEach(item => {
      const row = el('tr'); row.dataset.id = item.id || ''; row.dataset.test = 'data-row';
      renderRow(item).forEach(value => {
        const cell = el('td');
        if (value instanceof Node) cell.append(value);
        else if (Array.isArray(value)) value.forEach(node => cell.append(node));
        else cell.textContent = text(value);
        row.append(cell);
      }); body.append(row);
    });
    grid.append(head, body); target.replaceChildren(grid);
  }
  function matches(item, query) { return !query || String(item ?? '').toLowerCase().includes(query.toLowerCase()); }
  function detailButton(kind, item) {
    const button = el('button', 'button small', '详情'); button.type = 'button'; button.dataset.test = `${kind}-detail`;
    button.addEventListener('click', () => showDetails(kind, item)); return button;
  }
  function renderWms(snapshot) {
    const kind = state.wmsTab, query = $('wms-search').value.trim();
    const labels = { skus: ['商品主数据', '＋ 新增商品'], locations: ['库位主数据', '＋ 新增库位'], inventory: ['库存台账', '＋ 登记库存'] };
    set('wms-title', labels[kind][0]); set('wms-create', labels[kind][1]);
    document.querySelectorAll('[data-tab]').forEach(node => node.setAttribute('aria-pressed', String(node.dataset.tab === kind)));
    const rows = snapshot[kind].filter(item => matches(`${item.code || ''} ${item.name || ''} ${item.zone || ''} ${item.batch || ''} ${skuLabel(item.sku_id)} ${locationLabel(item.location_id)}`, query));
    set('wms-count', `${rows.length} / ${snapshot[kind].length} 条`);
    if (kind === 'skus') {
      table($('wms-table'), ['商品编码', '商品名称', '规格', '批次规则', '操作'], rows, sku => {
        const edit = el('button', 'button small', '编辑'); edit.dataset.write = 'true'; edit.dataset.test = 'sku-edit';
        edit.addEventListener('click', () => openEditor('sku', sku));
        return [el('strong', '', sku.code), sku.name, sku.spec, names[sku.batch_rule] || sku.batch_rule,
          [edit, actionButton('删除', 'sku.delete', { id: sku.id }, true, true)]];
      });
    } else if (kind === 'locations') {
      table($('wms-table'), ['库位编码', '区域', '坐标', '类型', '容量', '地图版本', '操作'], rows, location => {
        const edit = el('button', 'button small', '编辑'); edit.dataset.write = 'true'; edit.dataset.test = 'location-edit';
        edit.addEventListener('click', () => openEditor('location', location));
        return [el('strong', '', location.code), location.zone, `(${location.x}, ${location.y})`, badge(location.type), fmt(location.capacity), location.map_version, edit];
      });
    } else {
      table($('wms-table'), ['商品', '库位', '批次', '在库', '已预占', '可用', '有效期'], rows, stock => [
        skuLabel(stock.sku_id), locationLabel(stock.location_id), stock.batch, fmt(stock.qty), fmt(stock.reserved_qty),
        el('strong', '', fmt(stock.qty - stock.reserved_qty)), stock.expire_at ? new Date(stock.expire_at).toLocaleString('zh-CN') : '无有效期'
      ]);
    }
  }
  function renderOrders(snapshot) {
    const query = $('order-search').value.trim(), status = $('order-status').value;
    const rows = snapshot.orders.filter(order => (!status || order.status === status) && matches(`${order.code} ${order.requester}`, query));
    set('order-count', `${rows.length} / ${snapshot.orders.length} 单`);
    table($('orders-table'), ['订单号', '状态', '优先级', '明细 / 数量', '创建人', '创建时间', '操作'], rows.slice().reverse(), order => [
      el('strong', '', order.code), badge(order.status), el('span', `priority ${order.priority >= 8 ? 'high' : ''}`, `P${order.priority}`),
      `${array(order.lines).length} 项 / ${array(order.lines).reduce((sum, line) => sum + (line.qty || 0), 0)} 件`, order.requester,
      order.created_at ? new Date(order.created_at).toLocaleString('zh-CN') : '—', [detailButton('order', order),
        actionButton('预占', 'order.allocate', { id: order.id }, order.status === 'draft'),
        actionButton('释放', 'order.release', { id: order.id }, ['draft', 'allocated', 'released'].includes(order.status)),
        actionButton('取消', 'order.cancel', { id: order.id }, ['draft', 'allocated'].includes(order.status), true)]
    ]);
    diagnostics.filteredOrders = rows.map(item => item.id);
  }
  function renderTasks(snapshot) {
    const query = $('task-search').value.trim(), status = $('task-status').value, priority = $('task-priority').value, agv = $('task-agv').value;
    const rows = snapshot.tasks.filter(task => (!status || task.status === status) && (!priority || task.priority === Number(priority))
      && (!agv || (agv === '__none' ? !task.assigned_agv_id : task.assigned_agv_id === agv))
      && matches(`${task.id} ${task.code || ''} ${orderLabel(task.order_id)}`, query));
    set('task-count', `${rows.length} / ${snapshot.tasks.length} 项`);
    table($('tasks-table'), ['任务 / 来源订单', '状态', '取货 → 放货', 'AGV', '优先级', '计划 / 重试', '操作'], rows.slice().reverse(), task => {
      const title = el('span'); title.append(el('strong', '', task.code || short(task.id)), el('span', 'cell-sub', orderLabel(task.order_id)));
      const prioritySelect = el('select'); prioritySelect.setAttribute('aria-label', `任务 ${short(task.id)} 优先级`);
      prioritySelect.dataset.write = 'true'; prioritySelect.dataset.test = 'task-priority-edit';
      prioritySelect.dataset.unavailable = String(!['pending', 'assigned'].includes(task.status));
      for (let level = 1; level <= 10; level++) { const option = el('option', '', `P${level}`); option.value = level; prioritySelect.append(option); }
      prioritySelect.value = task.priority;
      prioritySelect.addEventListener('change', () => safely(() => command('task.priority', { id: task.id, priority: Number(prioritySelect.value) }), '优先级调整失败'));
      return [title, badge(task.status), `${locationLabel(task.pickup_location_id)} → ${locationLabel(task.drop_location_id)}`,
        task.assigned_agv_id ? agvLabel(task.assigned_agv_id) : '未指派', prioritySelect,
        `v${task.plan_version ?? 0} / ${task.retry_count ?? 0}`, [detailButton('task', task), actionButton('取消', 'task.cancel', { id: task.id }, ['pending', 'assigned'].includes(task.status), true)]];
    });
    diagnostics.filteredTasks = rows.map(item => item.id);
  }
  function eventPayloadMatches(event, id) {
    if (!id) return true;
    if (event.aggregateId === id) return true;
    const contains = value => value === id || (Array.isArray(value) ? value.some(contains)
      : value && typeof value === 'object' ? Object.values(value).some(contains) : false);
    return contains(event.payload);
  }
  function filteredEvents(snapshot) {
    const query = $('event-search').value.trim(), agv = $('event-agv').value, task = $('event-task').value, type = $('event-type').value;
    const from = $('event-from').value, to = $('event-to').value;
    const dateFrom = $('event-date-from').value, dateTo = $('event-date-to').value;
    return snapshot.events.filter(event => (!type || event.type === type) && eventPayloadMatches(event, agv) && eventPayloadMatches(event, task)
      && (from === '' || event.tick >= Number(from)) && (to === '' || event.tick <= Number(to))
      && (!dateFrom || Date.parse(event.occurredAt) >= Date.parse(dateFrom)) && (!dateTo || Date.parse(event.occurredAt) <= Date.parse(dateTo))
      && matches(`${event.type} ${event.aggregateId} ${JSON.stringify(event.payload)}`, query)).slice().reverse();
  }
  function resetEventHistory() {
    clearTimeout(state.eventTimer); state.eventRequestId++; state.eventRequest = null;
    state.eventResult = null; state.eventTypes = []; state.eventPage = 0; state.eventLoading = false;
  }
  function eventParams() {
    const params = new URLSearchParams({ offset: state.eventPage * 40, limit: 40 });
    const fields = { 'event-search': 'search', 'event-agv': 'agv_id', 'event-task': 'task_id',
      'event-type': 'type', 'event-from': 'from_tick', 'event-to': 'to_tick',
      'event-date-from': 'from_date', 'event-date-to': 'to_date' };
    for (const [id, name] of Object.entries(fields)) {
      const node = $(id), value = node.value.trim();
      if (value === '') continue;
      if (!node.validity.valid) throw new Error('事件查询条件格式不正确，请检查 tick 和日期。');
      params.set(name, name.endsWith('_date') ? new Date(value).toISOString() : value);
    }
    return params;
  }
  function queueEventRefresh() {
    clearTimeout(state.eventTimer);
    if (state.page === 'events' && !state.replay && state.live) {
      state.eventTimer = setTimeout(() => safely(refreshEvents, '事件历史查询失败'), 120);
    }
  }
  async function refreshEvents() {
    clearTimeout(state.eventTimer);
    if (state.replay || state.page !== 'events' || !state.live) return;
    const key = eventParams().toString();
    if (state.eventRequest?.key === key) return state.eventRequest.promise;
    const id = ++state.eventRequestId;
    state.eventLoading = true; state.dirty = true;
    const promise = request(`/api/events/page?${key}`).then(result => {
      // A response from an older filter, reset or playback session must not replace
      // the currently requested view, even when requests finish out of order.
      if (id !== state.eventRequestId || state.replay) return;
      state.eventResult = { ...result, key }; state.eventTypes = result.types;
      diagnostics.eventHistoryTotal = result.total; diagnostics.eventHistoryTick = result.tick;
      state.dirty = true;
    }).finally(() => {
      if (id === state.eventRequestId) { state.eventLoading = false; state.eventRequest = null; state.dirty = true; }
    });
    state.eventRequest = { key, promise };
    return promise;
  }
  function renderEvents(snapshot) {
    const size = 40;
    let rows, total;
    if (state.replay) {
      const history = filteredEvents(snapshot); total = history.length;
      state.eventPage = clamp(state.eventPage, 0, Math.max(0, Math.ceil(total / size) - 1));
      rows = history.slice(state.eventPage * size, (state.eventPage + 1) * size);
    } else {
      let key;
      try { key = eventParams().toString(); } catch { key = null; }
      const result = state.eventResult?.key === key ? state.eventResult : null;
      rows = result?.items ?? []; total = result?.total ?? 0;
    }
    const pagesCount = Math.max(1, Math.ceil(total / size));
    set('event-count', `${total} 条${state.replay ? ' · 当前历史帧' : ' · 完整持久化日志'}${state.eventLoading && !state.replay ? ' · 查询中' : ''}`);
    set('events-page', `${state.eventPage + 1} / ${pagesCount}`);
    $('events-prev').disabled = state.eventPage === 0; $('events-next').disabled = state.eventPage >= pagesCount - 1;
    table($('events-table'), ['tick', '事件类型', '聚合标识', '发生时间', '载荷', '操作'], rows, event => {
      const payload = el('span', '', JSON.stringify(event.payload || {})); payload.title = payload.textContent;
      return [el('strong', '', event.tick), event.type, event.aggregateId, event.occurredAt ? new Date(event.occurredAt).toLocaleString('zh-CN') : '—', payload, detailButton('event', event)];
    });
    diagnostics.filteredEvents = rows.map(item => item.id);
  }
  function showDetails(kind, item) {
    if (!item) { toast('当前快照中未找到该记录。'); return; }
    state.dialog = null;
    $('editor-form').classList.add('read-only'); set('dialog-title', { task: '任务详情', order: '订单详情', event: '事件详情' }[kind]);
    const body = $('dialog-body'); body.replaceChildren();
    const pairs = el('div', 'detail-pairs');
    const values = kind === 'task' ? [['任务标识', item.id], ['来源订单', orderLabel(item.order_id)], ['状态', names[item.status]], ['优先级', item.priority],
      ['取货点', locationLabel(item.pickup_location_id)], ['放货点', locationLabel(item.drop_location_id)], ['AGV', agvLabel(item.assigned_agv_id)], ['路径计划版本', item.plan_version]]
      : kind === 'order' ? [['单号', item.code], ['状态', names[item.status]], ['创建人', item.requester], ['优先级', item.priority]]
        : [['事件类型', item.type], ['tick', item.tick], ['发生时间', item.occurredAt], ['聚合标识', item.aggregateId]];
    values.forEach(([key, value]) => { const pair = el('div'); pair.append(el('span', '', key), document.createTextNode(text(value))); pairs.append(pair); });
    body.append(pairs);
    if (kind === 'order') {
      const lines = el('div', 'detail-block'); lines.append(el('strong', '', '订单明细'));
      array(item.lines).forEach(line => lines.append(el('div', '', `${skuLabel(line.sku_id)} × ${line.qty}`))); body.append(lines);
    }
    if (kind === 'task') {
      const plan = el('div', 'detail-block'); plan.dataset.test = 'task-plan';
      plan.append(el('strong', '', '完整时空路径 · task.plan.ticks'));
      const points = array(item.plan?.ticks);
      plan.append(document.createTextNode(points.length ? points.map(point => `T${point.tick}  (${point.x}, ${point.y})  ${names[point.action] || point.action || ''}`).join('\n') : '尚未生成路径计划'));
      body.append(plan);
    }
    const raw = el('details', 'detail-block'); raw.append(el('summary', '', '原始记录 JSON'), el('pre', '', JSON.stringify(item, null, 2))); body.append(raw);
    $('form-error').hidden = true;
    if (!$('editor-dialog').open) $('editor-dialog').showModal();
  }
  function field(body, label, name, options = {}) {
    const wrapper = el('label', `form-field ${options.wide ? 'wide' : ''}`, label);
    const input = el(options.choices ? 'select' : 'input'); input.name = name; input.id = `field-${name}`; input.dataset.test = `field-${name}`;
    if (options.choices) options.choices.forEach(([value, title]) => { const option = el('option', '', title); option.value = value; input.append(option); });
    else { input.type = options.type || 'text'; input.maxLength = options.maxLength || 120; }
    if (options.min !== undefined) input.min = options.min;
    if (options.max !== undefined) input.max = options.max;
    if (input.type === 'number') input.step = '1';
    input.required = options.required !== false;
    if (options.value !== undefined && options.value !== null) input.value = options.value;
    if (options.placeholder) input.placeholder = options.placeholder;
    wrapper.append(input); body.append(wrapper); return input;
  }
  function addOrderLine(value = {}) {
    const lines = $('order-line-list'); if (lines.children.length >= 100) { toast('最多 100 条明细。'); return; }
    const row = el('div', 'order-line'); row.dataset.test = 'order-line';
    const select = el('select'); select.name = 'line-sku'; select.required = true; select.setAttribute('aria-label', '明细商品');
    state.live.skus.forEach(sku => { const option = el('option', '', `${sku.code} · ${sku.name}`); option.value = sku.id; select.append(option); });
    if (value.sku_id) select.value = value.sku_id;
    const qty = el('input'); qty.type = 'number'; qty.name = 'line-qty'; qty.min = '1'; qty.max = '1000000'; qty.step = '1'; qty.required = true; qty.value = value.qty ?? 1; qty.setAttribute('aria-label', '明细数量');
    const remove = el('button', 'icon-button', '×'); remove.type = 'button'; remove.setAttribute('aria-label', '移除明细');
    remove.addEventListener('click', () => { if (lines.children.length > 1) row.remove(); else toast('订单至少需要一条明细。'); });
    row.append(select, qty, remove); lines.append(row);
  }
  function openEditor(kind, item = null) {
    if (!state.live || state.replay || state.busy) { toast('请先返回实时工作区并等待服务就绪。'); return; }
    state.dialog = { kind, item }; $('editor-form').classList.remove('read-only');
    const body = $('dialog-body'); body.replaceChildren(); $('form-error').hidden = true;
    set('dialog-title', `${item ? '编辑' : '新增'}${{ sku: '商品', location: '库位', inventory: '库存登记', order: '出库订单' }[kind]}`);
    set('save-dialog', kind === 'order' ? '创建草稿订单' : '保存');
    const live = state.live;
    if (kind === 'sku') {
      field(body, '商品编码 *', 'code', { value: item?.code }); field(body, '商品名称 *', 'name', { value: item?.name });
      field(body, '规格', 'spec', { value: item?.spec, required: false, maxLength: 200 });
      field(body, '批次规则 *', 'batch_rule', { choices: ['none', 'fifo', 'fefo'].map(value => [value, names[value]]), value: item?.batch_rule || 'fifo' });
    } else if (kind === 'location') {
      field(body, '库位编码 *', 'code', { value: item?.code }); field(body, '区域 *', 'zone', { value: item?.zone });
      field(body, '横坐标 X *', 'x', { type: 'number', min: 0, max: live.map.width - 1, value: item?.x ?? 0 });
      field(body, '纵坐标 Y *', 'y', { type: 'number', min: 0, max: live.map.height - 1, value: item?.y ?? 0 });
      field(body, '库位类型 *', 'type', { choices: ['floor', 'shelf', 'pick', 'drop', 'charger', 'blocked'].map(value => [value, names[value]]), value: item?.type || 'shelf' });
      field(body, '容量 *', 'capacity', { type: 'number', min: 1, max: 1000000000, value: item?.capacity ?? 100 });
      body.append(el('p', 'form-hint', '类型、容量或地图结构变化由服务端生成新地图版本；已有路径和占用约束由服务端校验。'));
    } else if (kind === 'inventory') {
      field(body, '商品 SKU *', 'sku_id', { wide: true, choices: live.skus.map(sku => [sku.id, `${sku.code} · ${sku.name}`]) });
      field(body, '库位 *', 'location_id', { wide: true, choices: live.locations.map(location => [location.id, `${location.code} · ${names[location.type] || location.type} (${location.x},${location.y})`]) });
      field(body, '入库数量 *', 'qty', { type: 'number', min: 0, max: 1000000000, value: 1 });
      field(body, '批次', 'batch', { required: false }); field(body, '有效期（本地时间）', 'expire_at', { type: 'datetime-local', required: false, wide: true });
    } else if (kind === 'order') {
      field(body, '出库单号 *', 'code', { value: `OUT-${Date.now()}` });
      field(body, '创建人 *', 'requester', { value: '演示用户' });
      field(body, '优先级 *', 'priority', { choices: Array.from({ length: 10 }, (_, index) => [index + 1, `P${index + 1}${index >= 7 ? ' · 高优先级' : ''}`]), value: 5 });
      field(body, '放货点', 'drop_location_id', { choices: [['', '默认放货点'], ...live.locations.filter(location => location.type === 'drop').map(location => [location.id, location.code])], required: false });
      const lines = el('div', 'order-lines'), header = el('div', 'order-lines-header');
      const add = el('button', 'button small', '＋ 添加明细'); add.type = 'button'; add.id = 'add-order-line'; add.dataset.test = 'add-order-line'; add.addEventListener('click', () => addOrderLine());
      header.append(el('strong', '', '出库明细'), add); const list = el('div'); list.id = 'order-line-list';
      lines.append(header, list); body.append(lines); addOrderLine();
      body.append(el('p', 'form-hint', '创建后为草稿。可在订单列表预占库存，再释放生成任务；缺货或非法状态会显示具体原因。'));
    }
    syncLocks(); $('editor-dialog').showModal();
  }
  async function submitEditor(event) {
    event.preventDefault(); if (!state.dialog || !$('editor-form').reportValidity()) return;
    const { kind, item } = state.dialog;
    const data = Object.fromEntries(new FormData($('editor-form')));
    Object.keys(data).forEach(key => { if (typeof data[key] === 'string') data[key] = data[key].trim(); });
    try {
      for (const [key, value] of Object.entries(data)) {
        if (['code', 'name', 'zone', 'requester'].includes(key) && !value) throw new Error('编码、名称、区域或创建人不能只含空格。');
      }
      if (kind === 'location') ['x', 'y', 'capacity'].forEach(key => { data[key] = Number(data[key]); });
      if (kind === 'inventory') {
        data.qty = Number(data.qty);
        if (data.expire_at) {
          if (!Number.isFinite(Date.parse(data.expire_at))) throw new Error('有效期不是合法日期。');
          data.expire_at = new Date(data.expire_at).toISOString();
        } else delete data.expire_at;
      }
      if (kind === 'order') {
        data.priority = Number(data.priority); if (!data.drop_location_id) delete data.drop_location_id;
        delete data['line-sku']; delete data['line-qty'];
        data.lines = [...$('order-line-list').children].map(row => ({ sku_id: row.querySelector('select').value, qty: Number(row.querySelector('input').value) }));
        if (!data.lines.length || data.lines.some(line => !line.sku_id || !Number.isInteger(line.qty) || line.qty < 1)) throw new Error('请选择商品并填写正整数数量。');
      }
      const action = kind === 'inventory' ? 'inventory.add' : `${kind}.${item ? 'update' : 'create'}`;
      if (item) data.id = item.id;
      await command(action, data); $('editor-dialog').close(); state.dialog = null;
    } catch (error) {
      set('form-error', `${error.code ? `[${error.code}] ` : ''}${error.message}`); $('form-error').hidden = false; report(error, '保存失败');
    }
  }
  function renderAnalytics(snapshot) {
    const stats = snapshot.stats;
    $('safety-stats').replaceChildren(...[['冲突请求', stats.conflicts], ['检测死锁', stats.deadlocks], ['实际恢复', stats.recoveries]].map(([label, value]) => {
      const box = el('div', 'safety-stat'); box.append(el('strong', '', fmt(value)), el('span', '', label)); return box;
    }));
    const distribution = $('task-distribution'), track = el('div', 'distribution-track'), labels = el('div', 'distribution-labels');
    taskStates.forEach((status, index) => {
      const count = snapshot.tasks.filter(task => task.status === status).length;
      if (!count) return;
      const segment = el('i'); segment.style.width = `${count / snapshot.tasks.length * 100}%`; segment.style.background = colors[index]; track.append(segment);
      const label = el('span'), dot = el('i', 'dot'); dot.style.background = colors[index]; label.append(dot, document.createTextNode(`${names[status]} ${count}`)); labels.append(label);
    });
    distribution.replaceChildren(track, labels);
    const utilization = array(stats.utilization);
    if (!utilization.length) empty($('utilization-chart'), '暂无设备利用率');
    else $('utilization-chart').replaceChildren(...utilization.map(item => barRow(item.code || short(item.id), number(item.utilization) ? item.utilization * 100 : 0,
      `${number(item.utilization) ? (item.utilization * 100).toFixed(1) : '—'}% / ${fmt(item.completed)}`, 'utilization-row')));
    const bottlenecks = array(stats.bottlenecks).slice(0, 8), maximum = Math.max(1, ...bottlenecks.map(item => item.count || 0));
    if (!bottlenecks.length) empty($('bottleneck-chart'), '暂无巷道访问记录');
    else $('bottleneck-chart').replaceChildren(...bottlenecks.map(item => barRow(`格点 ${item.cell}`, item.count / maximum * 100, `${fmt(item.count)} 次`, 'bottleneck-row')));
  }
  function barRow(label, width, value, test) {
    const row = el('div', 'bar-row'); row.dataset.test = test;
    const track = el('span', 'bar-track'), fill = el('i'); fill.style.width = `${clamp(width, 0, 100)}%`; track.append(fill);
    row.append(el('span', 'bar-label', label), track, el('span', 'bar-value', value)); return row;
  }
  function drawTrend() {
    if (state.page !== 'analytics') return;
    const target = $('throughput-chart'), size = canvasSize(target); if (!size) return;
    const context = target.getContext('2d'); if (!context) return;
    context.setTransform(size.dpr, 0, 0, size.dpr, 0, 0); context.clearRect(0, 0, size.width, size.height);
    const samples = state.replay ? state.replayFrames.slice(0, state.replayIndex + 1).filter(frame => number(frame.stats?.throughput)).map(frame => ({ tick: frame.tick, throughput: frame.stats.throughput })) : state.trend;
    const width = size.width - 66, height = size.height - 45, left = 48, top = 13;
    const max = Math.max(1, ...samples.map(point => point.throughput));
    context.font = '9px Segoe UI'; context.textAlign = 'right'; context.textBaseline = 'middle';
    for (let index = 0; index <= 4; index++) {
      const y = top + height * index / 4;
      context.strokeStyle = '#edf2f6'; context.lineWidth = 1; context.beginPath(); context.moveTo(left, y); context.lineTo(left + width, y); context.stroke();
      context.fillStyle = '#99aab8'; context.fillText(fmt(max * (1 - index / 4)), left - 8, y);
    }
    if (!samples.length) { context.textAlign = 'center'; context.fillText('暂无真实统计样本', size.width / 2, size.height / 2); return; }
    const firstTick = samples[0].tick, lastTick = samples.at(-1).tick;
    const points = samples.map(sample => ({ x: left + (sample.tick - firstTick) / Math.max(1, lastTick - firstTick) * width, y: top + height * (1 - sample.throughput / max) }));
    const gradient = context.createLinearGradient(0, top, 0, top + height); gradient.addColorStop(0, '#0a9d8a28'); gradient.addColorStop(1, '#0a9d8a01');
    context.beginPath(); context.moveTo(points[0].x, top + height); points.forEach(point => context.lineTo(point.x, point.y)); context.lineTo(points.at(-1).x, top + height); context.closePath(); context.fillStyle = gradient; context.fill();
    context.beginPath(); points.forEach((point, index) => index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y)); context.strokeStyle = '#0a9d8a'; context.lineWidth = 2; context.stroke();
    if (points.length === 1) { context.beginPath(); context.arc(points[0].x, points[0].y, 3, 0, Math.PI * 2); context.fillStyle = '#0a9d8a'; context.fill(); }
    context.fillStyle = '#99aab8'; context.textAlign = 'left'; context.fillText(`T ${firstTick}`, left, top + height + 19); context.textAlign = 'right'; context.fillText(`T ${lastTick}`, left + width, top + height + 19);
    set('trend-note', `${state.replay ? '历史记录' : '当前会话'} ${samples.length} 个真实样本 · 吞吐量单位：任务/小时`);
  }
  function stopReplay() { state.playing = false; set('replay-play', '▶ 播放'); }
  function replaySeek(index) {
    if (!state.replayFrames.length) throw new Error('请先载入历史帧。');
    state.replayIndex = clamp(Math.round(Number(index) || 0), 0, state.replayFrames.length - 1);
    state.replay = true; state.display = state.replayFrames[state.replayIndex]; state.dirty = true;
    clearTimeout(state.eventTimer); state.eventRequestId++; state.eventRequest = null; state.eventLoading = false;
    diagnostics.mode = 'replay'; diagnostics.replayIndex = state.replayIndex;
    $('replay-slider').value = state.replayIndex; syncLocks();
    if ($('editor-dialog').open) $('editor-dialog').close();
    return state.display;
  }
  async function loadReplay() {
    stopReplay(); const body = await request('/api/replay');
    if (!Array.isArray(body.frames) || !body.frames.length) throw new Error('服务端尚无历史帧，请先执行仿真或业务操作。');
    state.replayFrames = body.frames.map(frame => normalized(frame, state.live?.map));
    diagnostics.replayFrames = state.replayFrames.length;
    $('replay-slider').max = state.replayFrames.length - 1;
    ['replay-slider', 'replay-play', 'replay-prev', 'replay-next'].forEach(id => { $(id).disabled = false; });
    replaySeek(0); return { frames: state.replayFrames.length, tick: state.display.tick };
  }
  async function returnLive() {
    stopReplay(); await refreshSnapshot();
    state.replay = false; state.display = state.live; state.dirty = true;
    queueEventRefresh();
    diagnostics.mode = 'live'; diagnostics.replayIndex = null; syncLocks();
    return state.live;
  }
  function renderReplay() {
    if (!state.replayFrames.length) return;
    set('replay-position', `${state.replayIndex + 1} / ${state.replayFrames.length} · T ${state.replayFrames[state.replayIndex].tick}`);
    $('replay-prev').disabled = state.replayIndex === 0; $('replay-next').disabled = state.replayIndex === state.replayFrames.length - 1;
    if (!state.replay) { set('replay-note', '当前展示实时数据。拖动滑块可再次进入历史回放。'); return; }
    const frame = state.display;
    set('replay-note', `正在展示历史记录 ${state.replayIndex + 1}，tick ${frame.tick}；播放每 500ms 前进一条存档记录。`);
    $('replay-summary').replaceChildren(...[['tick', frame.tick], ['AGV', frame.agvs.length], ['任务', frame.tasks.length], ['事件', frame.events.length], ['地图版本', frame.map.version]].map(([key, value]) => el('span', '', `${key}：${value}`)));
    recentEvents($('replay-events'), frame.events, 10);
  }
  function renderUi() {
    state.dirty = false;
    const snapshot = state.display;
    if (!snapshot) return;
    renderShared(snapshot);
    if (state.page === 'overview') { renderFleet(snapshot); recentEvents($('recent-events'), snapshot.events); }
    if (state.page === 'wms') renderWms(snapshot);
    if (state.page === 'orders') renderOrders(snapshot);
    if (state.page === 'tasks') renderTasks(snapshot);
    if (state.page === 'events') renderEvents(snapshot);
    if (state.page === 'analytics') renderAnalytics(snapshot);
    if (state.page === 'replay') renderReplay();
    syncLocks();
    diagnostics.displayTick = snapshot.tick;
    set('footer-diagnostics', `${state.replay ? '回放' : '实时'} T ${snapshot.tick} · ${snapshot.agvs.length} AGV · ${diagnostics.fps.toFixed(1)} FPS`);
  }
  function initialize() {
    document.querySelectorAll('[id]').forEach(node => { if (!node.dataset.test) node.dataset.test = node.id; });
    document.querySelectorAll('nav [data-page]').forEach(node => { node.dataset.test = `nav-${node.dataset.page}`; });
    ['wms-create', 'new-order'].forEach(id => { $(id).dataset.write = 'true'; });
    setOptions($('order-status'), orderStates.map(value => [value, names[value]]), '全部订单状态');
    setOptions($('task-status'), taskStates.map(value => [value, names[value]]), '全部任务状态');
    setOptions($('fleet-status'), agvStates.map(value => [value, names[value]]), '全部状态');
    setOptions($('task-priority'), Array.from({ length: 10 }, (_, index) => [index + 1, `P${index + 1}`]), '全部优先级');
    $('dismiss-error').addEventListener('click', () => { $('error-banner').hidden = true; });
    $('refresh').addEventListener('click', () => safely(refreshSnapshot, '刷新快照失败'));
    document.querySelectorAll('[data-command]').forEach(node => {
      node.dataset.test = node.dataset.command;
      node.addEventListener('click', () => safely(() => command(node.dataset.command), '命令执行失败'));
    });
    document.querySelectorAll('[data-speed]').forEach(node => node.addEventListener('click', () => safely(() => command('sim.speed', { speed: Number(node.dataset.speed) }), '调整速度失败')));
    document.querySelectorAll('[data-inject]').forEach(node => node.addEventListener('click', () => safely(() => {
      if (!$('inject-agv').value) throw new Error('请先选择一台 AGV。');
      return command(`inject.${node.dataset.inject}`, { agv_id: $('inject-agv').value });
    }, '设备操作失败')));
    document.querySelectorAll('[data-block]').forEach(node => node.addEventListener('click', () => safely(() => {
      if (!$('block-cell').value) throw new Error('当前没有可操作的封路节点。');
      const [x, y] = $('block-cell').value.split(',').map(Number);
      return command(`inject.${node.dataset.block}`, { x, y });
    }, '地图操作失败')));
    $('reset-sim').addEventListener('click', () => safely(() => {
      if (!$('seed').reportValidity()) return;
      if (!window.confirm('重置将清空后端当前业务与历史记录，并用指定 seed 重新初始化。确定继续？')) return;
      return command('sim.reset', $('seed').value === '' ? {} : { seed: Number($('seed').value) }).then(result => {
        state.trend = []; state.replayFrames = []; diagnostics.replayFrames = 0;
        ['replay-slider', 'replay-play', 'replay-prev', 'replay-next'].forEach(id => { $(id).disabled = true; });
        set('replay-position', '尚未载入'); return result;
      });
    }, '重置失败'));
    $('map-agv').addEventListener('change', () => selectAgv($('map-agv').value));
    ['show-paths', 'show-history', 'show-links', 'show-grid', 'fleet-status'].forEach(id => $(id).addEventListener('change', () => { state.dirty = true; }));
    document.querySelectorAll('[data-tab]').forEach(node => node.addEventListener('click', () => { state.wmsTab = node.dataset.tab; state.dirty = true; }));
    $('wms-create').addEventListener('click', () => openEditor({ skus: 'sku', locations: 'location', inventory: 'inventory' }[state.wmsTab]));
    $('new-order').addEventListener('click', () => openEditor('order'));
    $('editor-form').addEventListener('submit', submitEditor);
    ['close-dialog', 'cancel-dialog'].forEach(id => $(id).addEventListener('click', () => $('editor-dialog').close()));
    $('editor-dialog').addEventListener('close', () => { state.dialog = null; });
    const filterIds = ['wms-search', 'order-search', 'order-status', 'task-search', 'task-status', 'task-priority', 'task-agv',
      'event-search', 'event-agv', 'event-task', 'event-type', 'event-from', 'event-to', 'event-date-from', 'event-date-to'];
    filterIds.forEach(id => {
      const node = $(id), changed = () => { state.dirty = true; if (id.startsWith('event-')) { state.eventPage = 0; queueEventRefresh(); } };
      node.addEventListener('input', changed); node.addEventListener('change', changed);
    });
    $('clear-event-filters').addEventListener('click', () => { filterIds.filter(id => id.startsWith('event-')).forEach(id => { $(id).value = ''; }); state.eventPage = 0; state.dirty = true; queueEventRefresh(); });
    $('events-prev').addEventListener('click', () => { state.eventPage--; state.dirty = true; queueEventRefresh(); });
    $('events-next').addEventListener('click', () => { state.eventPage++; state.dirty = true; queueEventRefresh(); });
    $('load-replay').addEventListener('click', () => safely(loadReplay, '载入回放失败'));
    ['exit-replay', 'return-live'].forEach(id => $(id).addEventListener('click', () => safely(returnLive, '返回实时失败')));
    $('replay-slider').addEventListener('input', () => { stopReplay(); replaySeek(Number($('replay-slider').value)); });
    $('replay-prev').addEventListener('click', () => { stopReplay(); replaySeek(state.replayIndex - 1); });
    $('replay-next').addEventListener('click', () => { stopReplay(); replaySeek(state.replayIndex + 1); });
    $('replay-play').addEventListener('click', () => {
      if (state.playing) { stopReplay(); return; }
      if (state.replayIndex === state.replayFrames.length - 1) replaySeek(0);
      else replaySeek(state.replayIndex);
      state.playing = true; state.replayLast = performance.now(); set('replay-play', 'Ⅱ 暂停');
    });
    window.addEventListener('hashchange', () => navigate(location.hash.slice(1)));
    window.addEventListener('resize', () => { floorKey = ''; state.dirty = true; });
    document.addEventListener('visibilitychange', () => { fpsStart = performance.now(); fpsCount = 0; });
    navigate(location.hash.slice(1) || 'overview'); syncLocks(); connect();
    safely(refreshSnapshot, '初始快照读取失败');
    setInterval(() => { if (!state.connected) safely(refreshSnapshot, '断线后快照读取失败'); }, 5000);
    window.addEventListener('beforeunload', () => state.stream?.close());
  }
  let fpsStart = performance.now(), fpsCount = 0;
  function animate(now) {
    diagnostics.frames++; fpsCount++;
    const elapsed = now - fpsStart;
    if (elapsed >= 1000) {
      diagnostics.fps = Number((fpsCount * 1000 / elapsed).toFixed(2)); fpsStart = now; fpsCount = 0;
      set('fps', diagnostics.fps.toFixed(1));
    }
    try {
      if (state.playing && now - state.replayLast >= 500) {
        state.replayLast = now;
        if (state.replayIndex + 1 < state.replayFrames.length) replaySeek(state.replayIndex + 1); else stopReplay();
      }
      if (state.dirty && now - state.lastUi >= 80) { renderUi(); state.lastUi = now; }
      drawMap(); drawTrend();
    } catch (error) {
      const message = error?.message || String(error);
      if (diagnostics.lastRenderError !== message) { diagnostics.lastRenderError = message; report(error, '界面绘制失败'); }
    }
    requestAnimationFrame(animate);
  }
  const nextPaint = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  window.__wmsTest = {
    ready: async () => { await refreshSnapshot(); renderUi(); drawMap(); await nextPaint(); return diagnostics; },
    command: async (action, payload) => {
      try { const result = await command(action, payload); renderUi(); drawMap(); await nextPaint(); return result; }
      catch (error) { report(error, '测试命令失败'); throw error; }
    },
    navigate: async page => { navigate(page); renderUi(); if (page === 'events') await refreshEvents(); renderUi(); drawMap(); await nextPaint(); return state.page; },
    selectAgv: async id => { selectAgv(id); renderUi(); drawMap(); await nextPaint(); return diagnostics.renderedAgvs.find(agv => agv.id === id) || null; },
    loadReplay: async () => { const result = await loadReplay(); renderUi(); drawMap(); await nextPaint(); return result; },
    seekReplay: async index => { const frame = replaySeek(index); renderUi(); drawMap(); await nextPaint(); return frame; },
    returnLive: async () => { await returnLive(); renderUi(); drawMap(); await nextPaint(); return diagnostics; },
    filter: async (page, values = {}) => {
      const allowed = {
        tasks: { search: 'task-search', status: 'task-status', priority: 'task-priority', agv: 'task-agv' },
        orders: { search: 'order-search', status: 'order-status' },
        events: { search: 'event-search', agv: 'event-agv', task: 'event-task', type: 'event-type', from: 'event-from', to: 'event-to', dateFrom: 'event-date-from', dateTo: 'event-date-to' }
      };
      if (!allowed[page]) throw new Error('筛选页面必须为 tasks、orders 或 events。');
      navigate(page); renderUi();
      Object.entries(values).forEach(([key, value]) => { if (!allowed[page][key]) throw new Error(`未知筛选字段 ${key}`); $(allowed[page][key]).value = value ?? ''; });
      state.eventPage = 0; state.dirty = true; if (page === 'events') await refreshEvents(); renderUi(); await nextPaint();
      return page === 'tasks' ? diagnostics.filteredTasks : page === 'orders' ? diagnostics.filteredOrders : diagnostics.filteredEvents;
    },
    openForm: (kind, id) => { const collection = { sku: 'skus', location: 'locations' }[kind]; openEditor(kind, id && collection ? state.live[collection].find(item => item.id === id) : null); },
    getSnapshot: () => structuredClone(state.display),
    getLiveSnapshot: () => structuredClone(state.live),
    getDiagnostics: () => structuredClone(diagnostics)
  };
  initialize(); requestAnimationFrame(animate);
})();
