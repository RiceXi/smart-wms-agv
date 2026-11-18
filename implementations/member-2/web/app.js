/* 智能仓储 WMS + AGV 调度控制台 —— 前端脚本
 *
 * 接口依据：implementations/member-2/docs/api-contract.md（冻结版 v1）
 * 约束：
 *   1. 所有业务状态（AGV 位置/状态/电量、任务、订单、告警、路径）均来自后端接口；
 *   2. 前端仅对 AGV 绘制坐标做视觉插值，不改写业务状态、不参与任何判定；
 *   3. 动画统一由 requestAnimationFrame 驱动；
 *   4. 所有请求失败都必须显示中文提示。
 */
(function () {
  'use strict';

  /* ========================= 常量 ========================= */

  var API_BASE = '/api/v1';
  var INTERP_MS = 280;            // AGV 视觉插值时长（仅影响绘制位置）
  var MAX_EVENTS = 400;           // 内存中保留的事件条数
  var SNAPSHOT_DEBOUNCE_MS = 220; // SSE 推送后的快照拉取节流
  var ANALYTICS_REFRESH_MS = 2500;// 分析页刷新间隔
  var LOW_BATTERY = 20;           // 低电量阈值（%）
  var FALLBACK_POLL_MS = 3000;    // 事件流断开时的兜底轮询

  var CELL_STYLE = {
    floor:   { fill: '#e9ecef', stroke: '#d5d9de', label: '地面' },
    shelf:   { fill: '#59636e', stroke: '#3e4753', label: '货架' },
    pick:    { fill: '#f2a03d', stroke: '#c2791b', label: '拣选点' },
    drop:    { fill: '#9b59d0', stroke: '#6f37a0', label: '放货点' },
    charger: { fill: '#37b96b', stroke: '#1f7d46', label: '充电位' },
    blocked: { fill: '#e0453c', stroke: '#9c2b24', label: '封路' }
  };

  var AGV_STYLE = {
    idle:      { color: '#8d99a6', label: '空闲' },
    to_pickup: { color: '#3b82f6', label: '前往取货' },
    loading:   { color: '#f59e0b', label: '取货确认' },
    to_dropoff:{ color: '#06b6d4', label: '前往放货' },
    unloading: { color: '#a855f7', label: '放货确认' },
    charging:  { color: '#22c55e', label: '充电' },
    fault:     { color: '#ef4444', label: '故障' },
    offline:   { color: '#4b5563', label: '离线' }
  };

  var ORDER_STATUS = {
    draft: '草稿', allocated: '已预占', released: '已生成任务',
    completed: '已完成', cancelled: '已取消', failed: '执行失败'
  };

  var TASK_STATUS = {
    pending: '待指派', assigned: '已指派', running: '执行中',
    completed: '已完成', cancelled: '已取消', failed: '已失败'
  };

  /* ========================= 工具 ========================= */

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }
  function esc(v) {
    return String(v === null || v === undefined ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function isNum(v) { return typeof v === 'number' && isFinite(v); }
  function num(v, dflt) { return isNum(v) ? v : (dflt === undefined ? 0 : dflt); }
  function clamp(v, a, b) { return Math.min(b, Math.max(a, v)); }
  function arr(v) { return Array.isArray(v) ? v : []; }
  function firstNum(list) {
    for (var i = 0; i < list.length; i++) { if (isNum(list[i])) return list[i]; }
    return null;
  }
  function extractItems(data) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.items)) return data.items;
    if (data && Array.isArray(data.content)) return data.content;
    if (data && Array.isArray(data.data)) return data.data;
    return [];
  }
  function shortId(id) {
    var s = String(id === null || id === undefined ? '' : id);
    return s.length > 12 ? s.slice(0, 11) + '…' : s;
  }
  function agvNumber(id) {
    var m = String(id || '').match(/(\d+)\s*$/);
    return m ? m[1] : String(id || '');
  }
  function fmtTime(v) {
    if (!v) return '--';
    var d = new Date(v);
    if (isNaN(d.getTime())) return String(v);
    return d.toLocaleTimeString('zh-CN', { hour12: false });
  }

  /* ========================= 全局状态 ========================= */

  var state = {
    meta: { width: 24, height: 24, tickSeconds: 1, mapId: '', mapVersion: '', seed: null },
    sim: { tick: 0, running: false, speed: 1, seed: null, scenarioId: null, mapVersion: null, lastEventSeq: 0 },
    map: null,
    agvs: [],
    tasks: [],
    orders: [],
    alerts: [],
    stats: {},
    summary: null,
    utilization: [],
    throughput: [],
    bottlenecks: [],
    events: [],
    skus: [],
    locations: [],
    scenarios: [],
    orderView: [],
    taskView: [],
    selectedAgvId: null,
    agvDetail: null,
    focusUntil: 0,
    activeTab: 'orders',
    connected: false
  };

  var dirty = {
    top: true, map: true, orders: true, tasks: true, agvs: true,
    analysis: true, events: true, roadblock: true
  };

  var agvRender = new Map();   // 仅用于绘制插值，不参与任何业务判定
  var view = { cell: 20, ox: 0, oy: 0, w: 0, h: 0 };
  var locById = new Map();

  /* ========================= 错误与提示 ========================= */

  function ApiError(message, status, code, details) {
    this.name = 'ApiError';
    this.message = message;
    this.status = status || 0;
    this.code = code || 'ERROR';
    this.details = details || null;
  }
  ApiError.prototype = Object.create(Error.prototype);

  function toast(message, kind, timeout) {
    var host = $('#toast-host');
    if (!host) return;
    var box = document.createElement('div');
    box.className = 'toast ' + (kind || 'info');
    var text = document.createElement('span');
    text.textContent = String(message);
    box.appendChild(text);
    var close = document.createElement('button');
    close.className = 'toast-close';
    close.type = 'button';
    close.textContent = '×';
    close.addEventListener('click', function () { if (box.parentNode) box.parentNode.removeChild(box); });
    box.appendChild(close);
    host.appendChild(box);
    var life = timeout || (kind === 'error' ? 9000 : 3500);
    window.setTimeout(function () { if (box.parentNode) box.parentNode.removeChild(box); }, life);
  }

  function reportError(err, prefix) {
    var msg = err && err.message ? err.message : String(err);
    toast((prefix ? prefix + '：' : '') + msg, 'error');
  }

  /* ========================= API 层 ========================= */

  function api(path, options) {
    var opts = options || {};
    var init = { method: opts.method || 'GET', headers: { Accept: 'application/json' } };
    if (opts.body !== undefined && opts.body !== null) {
      init.headers['Content-Type'] = 'application/json; charset=utf-8';
      init.body = JSON.stringify(opts.body);
    }
    return fetch(API_BASE + path, init).then(function (res) {
      if (!res.ok) {
        return res.text().then(function (raw) {
          var err = {};
          try { err = JSON.parse(raw).error || {}; } catch (e) { err = {}; }
          throw new ApiError(
            err.message || ('请求失败：' + path + '（HTTP ' + res.status + '）'),
            res.status,
            err.code || 'HTTP_ERROR',
            err.details || null
          );
        });
      }
      return res.text().then(function (raw) {
        if (!raw) return null;
        try { return JSON.parse(raw); } catch (e) {
          throw new ApiError('响应不是合法 JSON：' + path, res.status, 'BAD_JSON');
        }
      });
    }, function (e) {
      throw new ApiError('网络请求失败：无法连接后端服务（' + path + '）', 0, 'NETWORK_ERROR');
    });
  }

  /* ========================= 数据加载 ========================= */

  function applySnapshot(data) {
    if (!data || typeof data !== 'object') return;
    if (isNum(data.tick)) state.sim.tick = data.tick;
    if (typeof data.running === 'boolean') state.sim.running = data.running;
    if (isNum(data.speed)) state.sim.speed = data.speed;
    if (data.seed !== undefined && data.seed !== null) state.sim.seed = data.seed;
    if (data.scenarioId !== undefined && data.scenarioId !== null) state.sim.scenarioId = data.scenarioId;
    if (isNum(data.lastEventSeq)) state.sim.lastEventSeq = data.lastEventSeq;
    if (data.mapVersion && data.mapVersion !== state.sim.mapVersion) {
      state.sim.mapVersion = data.mapVersion;
      dirty.map = true;
    }
    if (Array.isArray(data.agvs)) state.agvs = data.agvs;
    if (Array.isArray(data.tasks)) state.tasks = data.tasks;
    if (Array.isArray(data.orders)) state.orders = data.orders;
    if (Array.isArray(data.alerts)) state.alerts = data.alerts;
    if (data.stats) state.stats = data.stats;
    syncAgvRender();
    if (!$('#order-status').value) state.orderView = state.orders.slice();
    if (!taskFilterActive()) state.taskView = state.tasks.slice();
    dirty.top = true; dirty.orders = true; dirty.tasks = true; dirty.agvs = true;
    dirty.events = true; dirty.analysis = true; dirty.roadblock = true;
  }

  function refreshSnapshot() {
    return api('/sim/state').then(applySnapshot);
  }

  var snapTimer = null;
  function scheduleSnapshot() {
    if (snapTimer) window.clearTimeout(snapTimer);
    snapTimer = window.setTimeout(function () {
      snapTimer = null;
      refreshSnapshot().catch(function (e) { reportError(e, '拉取仿真快照失败'); });
    }, SNAPSHOT_DEBOUNCE_MS);
  }

  function loadMap() {
    return api('/map').then(function (map) {
      state.map = map;
      if (map && map.width) state.meta.width = num(map.width, state.meta.width);
      if (map && map.height) state.meta.height = num(map.height, state.meta.height);
      dirty.map = true; dirty.roadblock = true; dirty.top = true;
      var empty = $('#map-empty');
      if (empty) {
        empty.hidden = true;
        empty.classList.remove('error');
        empty.textContent = '';
      }
    }).catch(function (e) {
      var empty = $('#map-empty');
      if (empty) {
        empty.hidden = false;
        empty.classList.add('error');
        empty.textContent = '地图加载失败：' + e.message;
      }
      throw e;
    });
  }

  function loadReference() {
    api('/skus?size=200').then(function (d) {
      state.skus = extractItems(d);
      renderDatalists();
    }).catch(function (e) { reportError(e, '加载 SKU 列表失败'); });

    api('/locations?size=200').then(function (d) {
      state.locations = extractItems(d);
      locById = new Map();
      state.locations.forEach(function (l) { if (l && l.id) locById.set(l.id, l); });
      renderDatalists();
    }).catch(function (e) { reportError(e, '加载库位列表失败'); });

    api('/scenarios').then(function (d) {
      state.scenarios = extractItems(d);
      renderScenarios();
    }).catch(function (e) { reportError(e, '加载场景列表失败'); });
  }

  function renderDatalists() {
    var dlAgv = $('#dl-agv'), dlSku = $('#dl-sku'), dlLoc = $('#dl-location');
    if (!dlAgv || !dlSku || !dlLoc) return;
    dlAgv.innerHTML = state.agvs.map(function (a) {
      return '<option value="' + esc(a.id) + '"></option>';
    }).join('');
    dlSku.innerHTML = state.skus.map(function (s) {
      return '<option value="' + esc(s.code) + '">' + esc(s.name || '') + '</option>';
    }).join('');
    dlLoc.innerHTML = state.locations.map(function (l) {
      var text = (l.code || l.id) + (l.type ? ' · ' + l.type : '');
      return '<option value="' + esc(l.code) + '">' + esc(text) + '</option>';
    }).join('');
  }

  function renderScenarios() {
    var sel = $('#reset-scenario');
    if (!sel) return;
    var cur = sel.value;
    sel.innerHTML = state.scenarios.map(function (s) {
      var id = typeof s === 'string' ? s : (s.id || s.scenarioId || s.name || '');
      var name = typeof s === 'string' ? s : (s.name || s.description || id);
      return '<option value="' + esc(id) + '">' + esc(name) + '</option>';
    }).join('');
    if (cur) sel.value = cur;
    if (!sel.value && state.sim.scenarioId) sel.value = state.sim.scenarioId;
    var seedInput = $('#reset-seed');
    if (seedInput && !seedInput.value) {
      seedInput.value = state.sim.seed !== null && state.sim.seed !== undefined ? state.sim.seed : (state.meta.seed || 20261118);
    }
  }

  /* ========================= AGV 绘制插值（仅视觉） ========================= */

  function syncAgvRender() {
    var now = performance.now();
    var seen = Object.create(null);
    state.agvs.forEach(function (a) {
      if (!a || !a.id || !isNum(a.x) || !isNum(a.y)) return;
      seen[a.id] = true;
      var r = agvRender.get(a.id);
      if (!r) {
        agvRender.set(a.id, { x: a.x, y: a.y, fx: a.x, fy: a.y, tx: a.x, ty: a.y, t0: now });
      } else if (r.tx !== a.x || r.ty !== a.y) {
        r.fx = r.x; r.fy = r.y;
        r.tx = a.x; r.ty = a.y;
        r.t0 = now;
      }
    });
    agvRender.forEach(function (_v, k) { if (!seen[k]) agvRender.delete(k); });
  }

  function interpAgv(r, now) {
    var t = clamp((now - r.t0) / INTERP_MS, 0, 1);
    var e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
    r.x = r.fx + (r.tx - r.fx) * e;
    r.y = r.fy + (r.ty - r.fy) * e;
  }

  /* ========================= Canvas 地图绘制 ========================= */

  function canvasCtx() {
    var canvas = $('#map-canvas');
    if (!canvas) return null;
    var wrap = $('#canvas-wrap');
    var dpr = window.devicePixelRatio || 1;
    var cw = wrap ? wrap.clientWidth : 0;
    var ch = wrap ? wrap.clientHeight : 0;
    if (cw <= 0 || ch <= 0) return null;
    var pw = Math.floor(cw * dpr), ph = Math.floor(ch * dpr);
    if (canvas.width !== pw || canvas.height !== ph) {
      canvas.width = pw; canvas.height = ph;
    }
    var ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx: ctx, w: cw, h: ch };
  }

  function computeView(w, h) {
    var W = num(state.meta.width, 24), H = num(state.meta.height, 24);
    var pad = 10;
    var cell = Math.floor(Math.min((w - pad * 2) / W, (h - pad * 2) / H));
    if (!isFinite(cell) || cell < 5) cell = 5;
    var mw = cell * W, mh = cell * H;
    view.cell = cell;
    view.ox = Math.round((w - mw) / 2);
    view.oy = Math.round((h - mh) / 2);
    view.w = w; view.h = h;
  }

  function cellPx(x, y) {
    return { px: view.ox + (x + 0.5) * view.cell, py: view.oy + (y + 0.5) * view.cell };
  }

  function parseCellId(id) {
    var m = String(id || '').match(/^(\d+)\s*,\s*(\d+)$/);
    if (!m) return null;
    return { x: parseInt(m[1], 10), y: parseInt(m[2], 10) };
  }

  function drawFrame(now) {
    var c = canvasCtx();
    if (!c) return;
    var ctx = c.ctx;
    computeView(c.w, c.h);
    ctx.clearRect(0, 0, c.w, c.h);

    var map = state.map;
    var cells = map && Array.isArray(map.cells) ? map.cells : [];
    var cellById = Object.create(null);
    cells.forEach(function (cell) { cellById[cell.id] = cell; });

    // 1. 地面与网格
    var W = num(state.meta.width, 24), H = num(state.meta.height, 24);
    ctx.fillStyle = '#f2f4f6';
    ctx.fillRect(view.ox, view.oy, view.cell * W, view.cell * H);
    ctx.strokeStyle = 'rgba(0,0,0,0.07)';
    ctx.lineWidth = 1;
    for (var gx = 0; gx <= W; gx++) {
      ctx.beginPath();
      ctx.moveTo(view.ox + gx * view.cell + 0.5, view.oy);
      ctx.lineTo(view.ox + gx * view.cell + 0.5, view.oy + view.cell * H);
      ctx.stroke();
    }
    for (var gy = 0; gy <= H; gy++) {
      ctx.beginPath();
      ctx.moveTo(view.ox, view.oy + gy * view.cell + 0.5);
      ctx.lineTo(view.ox + view.cell * W, view.oy + gy * view.cell + 0.5);
      ctx.stroke();
    }

    // 2. 边
    if (map && Array.isArray(map.edges)) {
      ctx.strokeStyle = 'rgba(60,80,100,0.16)';
      map.edges.forEach(function (e) {
        var a = parseCellId(e.from), b = parseCellId(e.to);
        if (!a || !b) return;
        var pa = cellPx(a.x, a.y), pb = cellPx(b.x, b.y);
        ctx.beginPath();
        ctx.moveTo(pa.px, pa.py);
        ctx.lineTo(pb.px, pb.py);
        ctx.stroke();
      });
    }

    // 3. 格点类型
    cells.forEach(function (cell) {
      var style = CELL_STYLE[cell.type] || CELL_STYLE.floor;
      var p = cellPx(num(cell.x), num(cell.y));
      var s = view.cell;
      var x = p.px - s / 2, y = p.py - s / 2;
      ctx.fillStyle = style.fill;
      ctx.strokeStyle = style.stroke;
      ctx.lineWidth = 1;
      if (cell.type === 'shelf') {
        ctx.fillRect(x + 1, y + 1, s - 2, s - 2);
        ctx.strokeRect(x + 1.5, y + 1.5, s - 3, s - 3);
        ctx.fillStyle = 'rgba(0,0,0,0.25)';
        ctx.fillRect(x + 3, y + s / 2 - 1, s - 6, 2);
      } else if (cell.type === 'pick' || cell.type === 'drop' || cell.type === 'charger') {
        roundRect(ctx, x + 1.5, y + 1.5, s - 3, s - 3, Math.max(2, s * 0.18));
        ctx.fill(); ctx.stroke();
        ctx.fillStyle = 'rgba(255,255,255,0.92)';
        if (cell.type === 'pick') {
          ctx.beginPath(); ctx.arc(p.px, p.py, Math.max(1.5, s * 0.13), 0, Math.PI * 2); ctx.fill();
        } else if (cell.type === 'drop') {
          ctx.fillRect(p.px - s * 0.12, p.py - s * 0.12, s * 0.24, s * 0.24);
        } else {
          ctx.font = 'bold ' + Math.max(7, Math.floor(s * 0.5)) + 'px sans-serif';
          ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          ctx.fillText('C', p.px, p.py + 0.5);
        }
      } else if (cell.type === 'blocked') {
        ctx.fillRect(x + 1, y + 1, s - 2, s - 2);
        ctx.strokeRect(x + 1.5, y + 1.5, s - 3, s - 3);
        hatch(ctx, x + 1, y + 1, s - 2, s - 2, 'rgba(255,255,255,0.55)');
      } else {
        ctx.fillRect(x + 0.5, y + 0.5, s - 1, s - 1);
      }
      if (cell.passable === false) {
        ctx.strokeStyle = '#c0392b';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(x + 3, y + 3); ctx.lineTo(x + s - 3, y + s - 3);
        ctx.moveTo(x + s - 3, y + 3); ctx.lineTo(x + 3, y + s - 3);
        ctx.stroke();
      }
    });

    // 4. 封路高亮
    var pulse = 0.5 + 0.5 * Math.sin(now / 260);
    if (map && Array.isArray(map.roadblocks)) {
      map.roadblocks.forEach(function (rb) {
        if (rb && rb.active === false) return;
        var pos = parseCellId(rb && rb.cellId);
        if (!pos) return;
        var p = cellPx(pos.x, pos.y), s = view.cell;
        ctx.save();
        ctx.fillStyle = 'rgba(224,69,60,0.35)';
        ctx.fillRect(p.px - s / 2 + 1, p.py - s / 2 + 1, s - 2, s - 2);
        hatch(ctx, p.px - s / 2 + 1, p.py - s / 2 + 1, s - 2, s - 2, 'rgba(255,255,255,0.8)');
        ctx.strokeStyle = 'rgba(255,80,70,' + (0.45 + 0.45 * pulse) + ')';
        ctx.lineWidth = 2;
        ctx.strokeRect(p.px - s / 2 + 1, p.py - s / 2 + 1, s - 2, s - 2);
        ctx.restore();
      });
    }

    // 5. 选中 AGV 的路径与轨迹
    var sel = state.selectedAgvId ? findAgv(state.selectedAgvId) : null;
    if (sel) drawSelectionOverlay(ctx, sel, now);

    // 6. AGV
    state.agvs.forEach(function (agv) {
      if (!agv || !isNum(agv.x) || !isNum(agv.y)) return;
      var r = agvRender.get(agv.id);
      if (!r) return;
      interpAgv(r, now);
      var p = cellPx(r.x, r.y);
      drawAgv(ctx, agv, p.px, p.py, now);
    });

    // 7. 地图边框
    ctx.strokeStyle = 'rgba(0,0,0,0.25)';
    ctx.lineWidth = 1;
    ctx.strokeRect(view.ox + 0.5, view.oy + 0.5, view.cell * W - 1, view.cell * H - 1);
  }

  function drawSelectionOverlay(ctx, agv, now) {
    var detail = state.agvDetail && state.agvDetail.id === agv.id ? state.agvDetail : null;
    var trail = (detail && Array.isArray(detail.trail) && detail.trail.length ? detail.trail
      : (Array.isArray(agv.trail) ? agv.trail : null));
    if (trail && trail.length > 1) {
      ctx.save();
      ctx.strokeStyle = 'rgba(56,189,248,0.5)';
      ctx.lineWidth = 2;
      ctx.setLineDash([]);
      ctx.beginPath();
      var started = false;
      trail.forEach(function (p) {
        if (!isNum(p.x) || !isNum(p.y)) return;
        var q = cellPx(p.x, p.y);
        if (!started) { ctx.moveTo(q.px, q.py); started = true; } else { ctx.lineTo(q.px, q.py); }
      });
      ctx.stroke();
      ctx.restore();
    }

    var plan = extractPlan(detail);
    if (plan && Array.isArray(plan.ticks) && plan.ticks.length > 1) {
      ctx.save();
      ctx.strokeStyle = '#facc15';
      ctx.lineWidth = 2.5;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      var st = false;
      plan.ticks.forEach(function (p) {
        if (!isNum(p.x) || !isNum(p.y)) return;
        var q = cellPx(p.x, p.y);
        if (!st) { ctx.moveTo(q.px, q.py); st = true; } else { ctx.lineTo(q.px, q.py); }
      });
      ctx.stroke();
      ctx.setLineDash([]);
      plan.ticks.forEach(function (p) {
        if (!isNum(p.x) || !isNum(p.y)) return;
        var q = cellPx(p.x, p.y);
        ctx.fillStyle = (p.action === 'wait') ? '#fb923c' : '#fde047';
        ctx.beginPath(); ctx.arc(q.px, q.py, 2.6, 0, Math.PI * 2); ctx.fill();
      });
      ctx.restore();
    }

    // 当前任务的取货 / 放货格点
    var task = agv.activeTaskId ? findTask(agv.activeTaskId) : null;
    if (task) {
      markEndpoint(ctx, task.pickupCell, '#f59e0b', '取');
      markEndpoint(ctx, task.dropCell, '#a855f7', '放');
    }
  }

  function markEndpoint(ctx, cellId, color, text) {
    var pos = parseCellId(cellId);
    if (!pos) return;
    var p = cellPx(pos.x, pos.y), s = view.cell;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.strokeRect(p.px - s / 2 + 2, p.py - s / 2 + 2, s - 4, s - 4);
    ctx.fillStyle = color;
    ctx.font = 'bold ' + Math.max(8, Math.floor(s * 0.42)) + 'px sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(text, p.px, p.py);
    ctx.restore();
  }

  function drawAgv(ctx, agv, px, py, now) {
    var style = AGV_STYLE[agv.status] || AGV_STYLE.idle;
    var s = view.cell;
    var radius = Math.max(5, s * 0.33);
    var battery = num(agv.battery, 0);
    var low = battery < LOW_BATTERY;

    // 电量外圈
    ctx.save();
    ctx.lineWidth = Math.max(2, s * 0.11);
    ctx.strokeStyle = 'rgba(0,0,0,0.15)';
    ctx.beginPath();
    ctx.arc(px, py, radius + 2.6, 0, Math.PI * 2);
    ctx.stroke();
    ctx.strokeStyle = low ? '#ff3b30' : (battery < 50 ? '#f59e0b' : '#22c55e');
    ctx.beginPath();
    ctx.arc(px, py, radius + 2.6, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * clamp(battery / 100, 0, 1));
    ctx.stroke();
    ctx.restore();

    // 低电量闪烁告警环
    if (low) {
      ctx.save();
      ctx.strokeStyle = 'rgba(255,59,48,' + (0.25 + 0.45 * (0.5 + 0.5 * Math.sin(now / 220))) + ')';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(px, py, radius + 5.5, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    // 主体
    ctx.save();
    ctx.fillStyle = style.color;
    ctx.strokeStyle = agv.status === 'fault' || agv.status === 'offline' ? '#7f1d1d' : 'rgba(255,255,255,0.9)';
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.arc(px, py, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    var label = agvNumber(agv.id);
    if (radius >= 8) {
      ctx.fillStyle = '#0b1017';
      ctx.font = 'bold ' + Math.max(8, Math.floor(radius * 1.05)) + 'px sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(label, px, py + 0.5);
    }
    ctx.restore();

    // 电量百分比（低电量醒目显示）
    if (low) {
      ctx.save();
      ctx.fillStyle = '#ff3b30';
      ctx.font = 'bold ' + Math.max(9, Math.floor(s * 0.4)) + 'px sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
      ctx.fillText(Math.round(battery) + '%', px, py - radius - 4);
      ctx.restore();
    }

    // 故障码
    if (agv.faultCode) {
      ctx.save();
      ctx.fillStyle = '#b91c1c';
      ctx.font = 'bold ' + Math.max(8, Math.floor(s * 0.36)) + 'px sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      ctx.fillText(String(agv.faultCode), px, py + radius + 3);
      ctx.restore();
    }

    // 选中态
    if (state.selectedAgvId === agv.id) {
      ctx.save();
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.arc(px, py, radius + 7, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
      if (now < state.focusUntil) {
        ctx.save();
        ctx.strokeStyle = 'rgba(56,189,248,' + (0.6 * (1 - (state.focusUntil - now) / 1500)) + ')';
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(px, py, radius + 10, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
    }
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  function hatch(ctx, x, y, w, h, color) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.4;
    for (var i = -h; i < w; i += 6) {
      ctx.beginPath();
      ctx.moveTo(x + i, y);
      ctx.lineTo(x + i + h, y + h);
      ctx.stroke();
    }
    ctx.restore();
  }

  /* ========================= 地图交互 ========================= */

  function bindMapEvents() {
    var canvas = $('#map-canvas');
    var wrap = $('#canvas-wrap');
    if (!canvas || !wrap) return;

    canvas.addEventListener('mousemove', function (ev) {
      var rect = canvas.getBoundingClientRect();
      var mx = ev.clientX - rect.left;
      var my = ev.clientY - rect.top;
      var hit = hitAgv(mx, my);
      var tip = $('#cell-tooltip');
      if (!tip) return;
      if (hit) {
        tip.hidden = false;
        tip.textContent = hit.agv.id + ' · ' + agvStatusLabel(hit.agv.status) +
          ' · 电量 ' + Math.round(num(hit.agv.battery, 0)) + '%' +
          (hit.agv.faultCode ? ' · ' + hit.agv.faultCode : '');
        tip.style.left = Math.min(mx, wrap.clientWidth - 190) + 'px';
        tip.style.top = Math.min(my, wrap.clientHeight - 40) + 'px';
        canvas.style.cursor = 'pointer';
        return;
      }
      var cell = cellAt(mx, my);
      if (cell) {
        var info = cellInfo(cell.id);
        tip.hidden = false;
        tip.textContent = '格点 ' + cell.id + ' · ' + info;
        tip.style.left = Math.min(mx, wrap.clientWidth - 190) + 'px';
        tip.style.top = Math.min(my, wrap.clientHeight - 40) + 'px';
        canvas.style.cursor = 'crosshair';
      } else {
        tip.hidden = true;
        canvas.style.cursor = 'default';
      }
    });

    canvas.addEventListener('mouseleave', function () {
      var tip = $('#cell-tooltip');
      if (tip) tip.hidden = true;
    });

    canvas.addEventListener('click', function (ev) {
      var rect = canvas.getBoundingClientRect();
      var mx = ev.clientX - rect.left;
      var my = ev.clientY - rect.top;
      var hit = hitAgv(mx, my);
      if (hit) { selectAgv(hit.agv.id, true); return; }
      var cell = cellAt(mx, my);
      if (cell) {
        var occupant = state.agvs.filter(function (a) { return a.x === cell.x && a.y === cell.y; })[0];
        if (occupant) selectAgv(occupant.id, true);
      }
    });
  }

  function cellAt(mx, my) {
    var cx = Math.floor((mx - view.ox) / view.cell);
    var cy = Math.floor((my - view.oy) / view.cell);
    var W = num(state.meta.width, 24), H = num(state.meta.height, 24);
    if (cx < 0 || cy < 0 || cx >= W || cy >= H) return null;
    return { x: cx, y: cy, id: cx + ',' + cy };
  }

  function hitAgv(mx, my) {
    var best = null, bestD = Infinity;
    state.agvs.forEach(function (agv) {
      var r = agvRender.get(agv.id);
      if (!r) return;
      var p = cellPx(r.x, r.y);
      var d = Math.hypot(p.px - mx, p.py - my);
      if (d < Math.max(8, view.cell * 0.5) && d < bestD) { best = agv; bestD = d; }
    });
    return best ? { agv: best } : null;
  }

  function cellInfo(id) {
    var map = state.map;
    if (map && Array.isArray(map.cells)) {
      for (var i = 0; i < map.cells.length; i++) {
        if (map.cells[i].id === id) {
          var cell = map.cells[i];
          var st = CELL_STYLE[cell.type] || CELL_STYLE.floor;
          return st.label + '（' + (cell.type || 'floor') + '）' +
            (cell.zone ? ' · 区域 ' + cell.zone : '') +
            (cell.passable === false ? ' · 不可通行' : '');
        }
      }
    }
    if (map && Array.isArray(map.roadblocks)) {
      var rb = map.roadblocks.filter(function (r) { return r.cellId === id && r.active !== false; })[0];
      if (rb) return '封路（' + (rb.reason || '无原因') + '）';
    }
    return '未知类型';
  }

  /* ========================= 详情与选择 ========================= */

  function findAgv(id) {
    for (var i = 0; i < state.agvs.length; i++) if (state.agvs[i].id === id) return state.agvs[i];
    return null;
  }
  function findTask(id) {
    for (var i = 0; i < state.tasks.length; i++) if (state.tasks[i].id === id) return state.tasks[i];
    return null;
  }
  function agvStatusLabel(st) {
    return (AGV_STYLE[st] || { label: st || '未知' }).label;
  }

  function extractPlan(detail) {
    if (!detail || typeof detail !== 'object') return null;
    var cands = [detail.plan, detail.currentPlan, detail.pathPlan, detail.path, detail.planDetail, detail.route];
    for (var i = 0; i < cands.length; i++) {
      var c = cands[i];
      if (c && Array.isArray(c.ticks) && c.ticks.length) return c;
    }
    if (Array.isArray(detail.ticks) && detail.ticks.length) {
      return { ticks: detail.ticks, edges: Array.isArray(detail.edges) ? detail.edges : [] };
    }
    return null;
  }

  function selectAgv(id, loadDetail) {
    state.selectedAgvId = id;
    state.focusUntil = performance.now() + 1500;
    dirty.agvs = true;
    if (loadDetail) {
      api('/agvs/' + encodeURIComponent(id)).then(function (detail) {
        state.agvDetail = detail;
        dirty.agvs = true;
        renderAgvDetail();
      }).catch(function (e) {
        reportError(e, '加载 AGV 详情失败');
        state.agvDetail = null;
        renderAgvDetail();
      });
    }
    renderAgvDetail();
  }

  function renderAgvDetail() {
    var box = $('#agv-detail');
    if (!box) return;
    var agv = state.selectedAgvId ? findAgv(state.selectedAgvId) : null;
    if (!agv) {
      box.innerHTML = '<div class="card-title">AGV 详情</div><div class="card-body muted">在地图上点击 AGV 查看详情。</div>';
      return;
    }
    var detail = state.agvDetail && state.agvDetail.id === agv.id ? state.agvDetail : null;
    var plan = extractPlan(detail);
    var trail = (detail && Array.isArray(detail.trail)) ? detail.trail : (Array.isArray(agv.trail) ? agv.trail : []);
    var task = agv.activeTaskId ? findTask(agv.activeTaskId) : null;
    var battery = num(agv.battery, 0);

    var rows = [
      ['编号', esc(agv.id)],
      ['状态', '<span class="badge ' + esc(agv.status) + '">' + esc(agvStatusLabel(agv.status)) + '</span>'],
      ['电量', '<span class="batt-text' + (battery < LOW_BATTERY ? ' low' : '') + '">' + Math.round(battery) + '%' + (battery < LOW_BATTERY ? '（低电量）' : '') + '</span>'],
      ['坐标', esc(agv.x) + ', ' + esc(agv.y)],
      ['当前任务', task ? esc(task.id) + '（' + esc(TASK_STATUS[task.status] || task.status) + '）' : (esc(agv.activeTaskId || '无'))],
      ['载货 SKU', esc(agv.payloadSku || '无')],
      ['故障码', agv.faultCode ? '<span style="color:#fca5a5">' + esc(agv.faultCode) + '</span>' : '无'],
      ['最后 tick', esc(agv.lastTick)],
      ['轨迹点', esc(trail.length)],
      ['路径点', plan ? esc(plan.ticks.length) + '（计划版本 ' + esc(plan.planVersion) + '）' : '未获取到路径']
    ];
    box.innerHTML = '<div class="card-title">AGV 详情 · ' + esc(agv.id) + '</div>' +
      '<div class="card-body"><dl class="kv">' +
      rows.map(function (r) { return '<dt>' + r[0] + '</dt><dd>' + r[1] + '</dd>'; }).join('') +
      '</dl></div>';
  }

  /* ========================= 顶部与面板渲染 ========================= */

  function renderTop() {
    var tick = $('#top-tick'), running = $('#top-running'), speed = $('#top-speed');
    var seed = $('#top-seed'), scen = $('#top-scenario'), mapv = $('#top-map-version');
    if (tick) tick.textContent = String(state.sim.tick);
    if (running) {
      running.textContent = state.sim.running ? '运行中' : '已暂停';
      running.parentNode.className = 'chip ' + (state.sim.running ? 'on' : '');
    }
    if (speed) speed.textContent = state.sim.speed + 'x';
    if (seed) seed.textContent = state.sim.seed === null || state.sim.seed === undefined ? '--' : String(state.sim.seed);
    if (scen) scen.textContent = state.sim.scenarioId || '--';
    if (mapv) {
      var v = (state.map && state.map.version) || state.sim.mapVersion || state.meta.mapVersion;
      mapv.textContent = '地图版本 ' + (v || '--');
    }
    $$('#speed-seg .seg-btn').forEach(function (b) {
      b.classList.toggle('active', Number(b.getAttribute('data-speed')) === num(state.sim.speed, 1));
    });
    var size = $('#map-size');
    if (size) size.textContent = num(state.meta.width, 24) + '×' + num(state.meta.height, 24);
    var rc = $('#roadblock-count');
    if (rc) rc.textContent = String(activeRoadblocks().length);
  }

  function activeRoadblocks() {
    var map = state.map;
    if (!map || !Array.isArray(map.roadblocks)) return [];
    return map.roadblocks.filter(function (rb) { return rb && rb.active !== false; });
  }

  function renderRoadblocks() {
    var box = $('#roadblock-list');
    if (!box) return;
    var list = activeRoadblocks();
    if (!list.length) { box.innerHTML = '<span class="muted">无</span>'; return; }
    box.innerHTML = list.map(function (rb) {
      return '<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">' +
        '<span class="badge failed">' + esc(rb.cellId || '') + '</span>' +
        '<span class="muted" style="flex:1;overflow-wrap:anywhere">' + esc(rb.reason || '未注明原因') + '</span>' +
        '<button class="btn tiny" data-act="unblock" data-id="' + esc(rb.id) + '">解除</button>' +
        '</div>';
    }).join('');
    var rc = $('#roadblock-count');
    if (rc) rc.textContent = String(list.length);
  }

  function renderOrders() {
    var tbl = $('#orders-table');
    if (!tbl) return;
    var view2 = state.orderView || [];
    if (!view2.length) {
      tbl.innerHTML = '<tbody><tr><td class="empty">暂无订单</td></tr></tbody>';
      return;
    }
    var head = '<thead><tr><th style="width:26%">单号</th><th style="width:16%">状态</th><th style="width:9%">优先级</th>' +
      '<th style="width:24%">明细</th><th style="width:11%">操作</th></tr></thead>';
    var body = '<tbody>' + view2.map(function (o) {
      var lines = arr(o.lines);
      var qty = lines.reduce(function (s, l) { return s + num(l.qty, 0); }, 0);
      var canRelease = o.status === 'draft' || o.status === 'allocated';
      var canCancel = o.status === 'draft' || o.status === 'allocated' || o.status === 'released';
      return '<tr>' +
        '<td>' + esc(o.code || o.id) + '<div class="payload">' + esc(shortId(o.id)) + '</div></td>' +
        '<td><span class="badge ' + esc(o.status) + '">' + esc(ORDER_STATUS[o.status] || o.status) + '</span></td>' +
        '<td>' + esc(o.priority) + '</td>' +
        '<td>' + lines.map(function (l) {
          return '<div>' + esc(l.skuCode || l.skuId || '') + ' × ' + esc(l.qty) + '</div>';
        }).join('') + '<div class="payload">合计 ' + qty + ' · 任务 ' + esc(arr(o.taskIds).length) + '</div></td>' +
        '<td>' +
        (canRelease ? '<button class="btn tiny" data-act="order-release" data-id="' + esc(o.id) + '">释放</button> ' : '') +
        (canCancel ? '<button class="btn tiny warn" data-act="order-cancel" data-id="' + esc(o.id) + '">取消</button>' : '') +
        '</td></tr>';
    }).join('') + '</tbody>';
    tbl.innerHTML = head + body;
  }

  function taskFilterActive() {
    return !!($('#task-status').value || $('#task-agv').value.trim() || $('#task-pmin').value || $('#task-pmax').value);
  }

  function renderTasks() {
    var tbl = $('#tasks-table');
    if (!tbl) return;
    var list = state.taskView || [];
    if (!list.length) {
      tbl.innerHTML = '<tbody><tr><td class="empty">暂无任务</td></tr></tbody>';
      return;
    }
    var head = '<thead><tr><th style="width:16%">任务</th><th style="width:15%">优先级</th><th style="width:13%">状态</th>' +
      '<th style="width:13%">取货</th><th style="width:13%">放货</th><th style="width:13%">AGV</th><th style="width:17%">操作</th></tr></thead>';
    var body = '<tbody>' + list.map(function (t) {
      var pick = t.pickupCell || cellOfLocation(t.pickupLocationId);
      var drop = t.dropCell || cellOfLocation(t.dropLocationId);
      var canCancel = t.status === 'pending' || t.status === 'assigned' || t.status === 'running';
      var canReassign = t.status === 'assigned' || t.status === 'running';
      return '<tr>' +
        '<td>' + esc(shortId(t.id)) + '<div class="payload">' + esc(t.type || '') + '</div></td>' +
        '<td><input class="inp" style="width:46px" type="number" min="1" max="10" value="' + esc(t.priority) +
        '" data-prio="' + esc(t.id) + '" /> <button class="btn link" data-act="task-prio" data-id="' + esc(t.id) + '">应用</button></td>' +
        '<td><span class="badge ' + esc(t.status) + '">' + esc(TASK_STATUS[t.status] || t.status) + '</span>' +
        '<div class="payload">重试 ' + esc(num(t.retryCount, 0)) + ' · v' + esc(num(t.planVersion, 0)) + '</div></td>' +
        '<td>' + esc(pick || '--') + '</td>' +
        '<td>' + esc(drop || '--') + '</td>' +
        '<td>' + esc(t.assignedAgvId || '--') + '</td>' +
        '<td>' +
        (canCancel ? '<button class="btn tiny warn" data-act="task-cancel" data-id="' + esc(t.id) + '">取消</button> ' : '') +
        (canReassign ? '<button class="btn tiny" data-act="task-reassign" data-id="' + esc(t.id) + '">重派</button> ' : '') +
        '<button class="btn tiny" data-act="task-timeline" data-id="' + esc(t.id) + '">时间线</button>' +
        '</td></tr>';
    }).join('') + '</tbody>';

    // 保留正在编辑的优先级输入框（表格随快照刷新时不应清空用户输入）
    var act = document.activeElement;
    var actId = (act && act.getAttribute) ? act.getAttribute('data-prio') : null;
    var actVal = (act && act.value !== undefined) ? act.value : null;

    tbl.innerHTML = head + body;

    if (actId) {
      var again = tbl.querySelector('[data-prio="' + actId + '"]');
      if (again) {
        again.focus();
        if (actVal !== null) again.value = actVal;
      }
    }
  }

  function cellOfLocation(locId) {
    var l = locById.get(locId);
    if (l && isNum(l.x) && isNum(l.y)) return l.x + ',' + l.y;
    return null;
  }

  function renderAgvs() {
    var box = $('#agv-list');
    if (!box) return;
    var filter = $('#agv-status').value;
    var list = state.agvs.filter(function (a) { return !filter || a.status === filter; });
    if (!list.length) { box.innerHTML = '<div class="muted">暂无 AGV</div>'; return; }
    box.innerHTML = list.map(function (a) {
      var style = AGV_STYLE[a.status] || AGV_STYLE.idle;
      var battery = num(a.battery, 0);
      var low = battery < LOW_BATTERY;
      var task = a.activeTaskId ? findTask(a.activeTaskId) : null;
      return '<div class="agv-item' + (state.selectedAgvId === a.id ? ' sel' : '') + '" data-act="focus-agv" data-id="' + esc(a.id) + '">' +
        '<span class="agv-dot" style="background:' + style.color + '"></span>' +
        '<div>' +
        '<div><span class="agv-name">' + esc(a.id) + '</span> ' +
        '<span class="badge ' + esc(a.status) + '">' + esc(style.label) + '</span></div>' +
        '<div class="agv-meta">任务 ' + esc(task ? shortId(task.id) : (a.activeTaskId ? shortId(a.activeTaskId) : '无')) +
        ' · 坐标 ' + esc(a.x) + ',' + esc(a.y) +
        (a.faultCode ? ' · <span style="color:#fca5a5">' + esc(a.faultCode) + '</span>' : '') +
        '</div>' +
        '<div class="batt' + (low ? ' low' : '') + '"><i style="width:' + clamp(battery, 0, 100) + '%"></i></div>' +
        '</div>' +
        '<div class="batt-text' + (low ? ' low' : '') + '">' + Math.round(battery) + '%</div>' +
        '</div>';
    }).join('');
  }

  function renderAnalysis() {
    var grid = $('#stat-grid');
    if (!grid) return;
    var s = state.summary || state.stats || {};
    var orders = s.orders || {};
    var tasks = s.tasks || {};
    var agvs = s.agvs || {};
    var cards = [
      ['订单总数', num(orders.total, state.orders.length), ''],
      ['任务总数', num(tasks.total, state.tasks.length), ''],
      ['AGV 总数', num(agvs.total, state.agvs.length), ''],
      ['已完成任务', num(s.completedTasks, 0), 'good'],
      ['取消任务', num(s.cancelledTasks, 0), ''],
      ['失败任务', num(s.failedTasks, 0), num(s.failedTasks, 0) > 0 ? 'bad' : ''],
      ['冲突数', num(s.conflicts, 0), num(s.conflicts, 0) > 0 ? 'warn' : ''],
      ['死锁数', num(s.deadlocks, 0), num(s.deadlocks, 0) > 0 ? 'bad' : ''],
      ['恢复数', num(s.recoveries, 0), 'good'],
      ['回退次数', num(s.rollbacks, 0), ''],
      ['平均耗时(tick)', num(s.avgTaskTicks, 0).toFixed(1), ''],
      ['当前告警', state.alerts.length, state.alerts.length > 0 ? 'warn' : '']
    ];
    grid.innerHTML = cards.map(function (c) {
      return '<div class="stat ' + c[2] + '"><div class="k">' + c[0] + '</div><div class="v">' + c[1] + '</div></div>';
    }).join('');

    var bt = $('#bottleneck-table');
    if (bt) {
      var rows = arr(state.bottlenecks);
      if (!rows.length) {
        bt.innerHTML = '<tbody><tr><td class="empty">暂无数据</td></tr></tbody>';
      } else {
        bt.innerHTML = '<thead><tr><th>巷道</th><th>占用次数</th><th>等待 tick</th></tr></thead><tbody>' +
          rows.map(function (r) {
            var id = r.cellId || r.id || r.aisle || r.segment || '';
            var count = firstNum([r.count, r.occupancy, r.visits, r.times, r.value]) || 0;
            var wait = firstNum([r.waitTicks, r.wait, r.totalWait]) || 0;
            return '<tr><td>' + esc(id) + '</td><td>' + esc(count) + '</td><td>' + esc(wait) + '</td></tr>';
          }).join('') + '</tbody>';
      }
    }

    drawCharts();
  }

  function drawCharts() {
    drawBarChart($('#chart-util'), state.utilization, {
      color: '#38bdf8', suffix: '%', max: 100,
      empty: '后端未返回利用率数据'
    });
    drawBarChart($('#chart-throughput'), state.throughput, {
      color: '#a855f7', suffix: '', max: 0, xLabel: 'tick',
      empty: '后端未返回吞吐数据'
    });
    var n1 = $('#chart-util-note'), n2 = $('#chart-throughput-note');
    if (n1) n1.textContent = state.utilization.length ? ('数据来源：GET /api/v1/analytics/utilization，共 ' + state.utilization.length + ' 台 AGV') : '数据来源：GET /api/v1/analytics/utilization（暂无数据）';
    if (n2) n2.textContent = state.throughput.length ? ('数据来源：GET /api/v1/analytics/throughput，共 ' + state.throughput.length + ' 个采样点') : '数据来源：GET /api/v1/analytics/throughput（暂无数据）';
  }

  function drawBarChart(canvas, items, opt) {
    if (!canvas) return;
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    var pw = Math.floor(w * dpr), ph = Math.floor(h * dpr);
    if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }
    var ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    var list = arr(items).filter(function (it) { return isNum(it.value); });
    if (!list.length) {
      ctx.fillStyle = '#6b7c8f';
      ctx.font = '12px sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(opt.empty || '暂无数据', w / 2, h / 2);
      return;
    }
    var padL = 34, padR = 8, padT = 12, padB = 22;
    var plotW = Math.max(10, w - padL - padR);
    var plotH = Math.max(10, h - padT - padB);
    var maxV = opt.max || 0;
    if (!maxV) {
      list.forEach(function (it) { if (it.value > maxV) maxV = it.value; });
      maxV = maxV || 1;
    }
    maxV = maxV * 1.12;

    // 坐标轴
    ctx.strokeStyle = 'rgba(140,160,180,0.25)';
    ctx.fillStyle = '#7d8fa1';
    ctx.lineWidth = 1;
    ctx.font = '9px sans-serif';
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    for (var i = 0; i <= 4; i++) {
      var y = padT + plotH - (plotH * i / 4);
      ctx.beginPath();
      ctx.moveTo(padL, y + 0.5); ctx.lineTo(padL + plotW, y + 0.5);
      ctx.stroke();
      ctx.fillText((maxV * i / 4).toFixed(maxV <= 10 ? 1 : 0), padL - 4, y);
    }

    var n = list.length;
    var slot = plotW / n;
    var bw = Math.max(2, Math.min(34, slot * 0.66));
    list.forEach(function (it, idx) {
      var bh = Math.max(1, plotH * clamp(it.value / maxV, 0, 1));
      var x = padL + slot * idx + (slot - bw) / 2;
      var y = padT + plotH - bh;
      var grad = ctx.createLinearGradient(0, y, 0, y + bh);
      grad.addColorStop(0, opt.color);
      grad.addColorStop(1, 'rgba(0,0,0,0.25)');
      ctx.fillStyle = grad;
      ctx.fillRect(x, y, bw, bh);
      if (n <= 24 || idx % Math.ceil(n / 20) === 0) {
        ctx.fillStyle = '#8fa1b3';
        ctx.font = '9px sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        ctx.fillText(String(it.label), x + bw / 2, padT + plotH + 4);
      }
    });
  }

  function renderEvents() {
    var tbl = $('#events-table');
    if (!tbl) return;
    var filter = $('#event-filter').value.trim().toLowerCase();
    var list = state.events.filter(function (e) {
      return !filter || String(e.type || '').toLowerCase().indexOf(filter) >= 0;
    });
    if (!list.length) {
      tbl.innerHTML = '<tbody><tr><td class="empty">暂无事件</td></tr></tbody>';
    } else {
      tbl.innerHTML = '<thead><tr><th style="width:10%">tick</th><th style="width:34%">类型</th><th style="width:22%">聚合 ID</th><th style="width:34%">载荷</th></tr></thead><tbody>' +
        list.map(function (e) {
          var p = '';
          try { p = JSON.stringify(e.payload || {}); } catch (err) { p = ''; }
          if (p && p.length > 110) p = p.slice(0, 110) + '…';
          return '<tr><td>' + esc(e.tick) + '</td>' +
            '<td class="ev-type">' + esc(e.type) + '</td>' +
            '<td>' + esc(e.aggregateId || '--') + '</td>' +
            '<td class="payload">' + esc(p) + '</td></tr>';
        }).join('') + '</tbody>';
    }

    var alerts = $('#alert-list');
    if (alerts) {
      if (!state.alerts.length) {
        alerts.innerHTML = '<div class="muted">暂无告警</div>';
      } else {
        alerts.innerHTML = state.alerts.map(function (a) {
          var sev = String(a.severity || a.level || a.type || '').toLowerCase();
          var cls = sev.indexOf('error') >= 0 || sev.indexOf('fatal') >= 0 ? 'sev-error'
            : (sev.indexOf('info') >= 0 ? 'sev-info' : '');
          var title = a.message || a.title || a.description || a.type || '告警';
          var meta = [isNum(a.tick) ? 'tick ' + a.tick : '', a.aggregateId || a.agvId || a.taskId || ''].filter(Boolean).join(' · ');
          return '<div class="alert-item ' + cls + '"><div class="alert-title">' + esc(title) + '</div>' +
            '<div class="alert-meta">' + esc(meta) + (a.code ? ' · ' + esc(a.code) : '') + '</div></div>';
        }).join('');
      }
    }
  }

  /* ========================= 分析数据 ========================= */

  function loadAnalytics() {
    api('/analytics/summary').then(function (d) {
      state.summary = d || {};
      dirty.analysis = true;
    }).catch(function (e) { reportError(e, '加载汇总统计失败'); });

    api('/analytics/utilization').then(function (d) {
      state.utilization = extractUtilization(d);
      dirty.analysis = true;
    }).catch(function (e) {
      state.utilization = [];
      reportError(e, '加载 AGV 利用率失败');
    });

    api('/analytics/throughput?window=600').then(function (d) {
      var pts = extractThroughput(d);
      if (pts.length) { state.throughput = pts; dirty.analysis = true; return; }
      return fallbackThroughput();
    }).catch(function (e) {
      reportError(e, '加载吞吐数据失败');
      return fallbackThroughput();
    });

    api('/analytics/bottlenecks?top=10').then(function (d) {
      state.bottlenecks = extractItems(d);
      dirty.analysis = true;
    }).catch(function (e) {
      state.bottlenecks = [];
      reportError(e, '加载瓶颈巷道失败');
    });
  }

  function fallbackThroughput() {
    return api('/events?type=task.completed&size=200').then(function (d) {
      var items = extractItems(d);
      var counts = Object.create(null);
      items.forEach(function (e) {
        var t = e.tick;
        if (!isNum(t)) return;
        counts[t] = (counts[t] || 0) + 1;
      });
      state.throughput = Object.keys(counts).map(function (k) {
        return { tick: Number(k), value: counts[k] };
      }).sort(function (a, b) { return a.tick - b.tick; });
      dirty.analysis = true;
    }).catch(function (e) { reportError(e, '加载任务完成事件失败'); });
  }

  function extractUtilization(data) {
    var out = [];
    function push(v) {
      if (Array.isArray(v)) { v.forEach(push); return; }
      if (isNum(v)) { out.push({ label: '', value: v }); return; }
      if (!v || typeof v !== 'object') return;
      var id = v.agvId || v.id || v.agv || v.name || v.code;
      var val = firstNum([v.utilization, v.utilizationRate, v.rate, v.value, v.percent, v.busyRatio, v.usage]);
      if (id !== undefined && val !== null) {
        out.push({ label: String(id), value: val <= 1 ? val * 100 : val });
        return;
      }
      Object.keys(v).forEach(function (k) {
        if (isNum(v[k])) out.push({ label: k, value: v[k] <= 1 ? v[k] * 100 : v[k] });
      });
    }
    if (Array.isArray(data)) push(data);
    else if (data && typeof data === 'object') {
      var keys = ['items', 'agvs', 'utilizations', 'list', 'data'];
      var found = false;
      keys.forEach(function (k) {
        if (Array.isArray(data[k])) { found = true; push(data[k]); }
      });
      if (!found) push(data);
    }
    return out;
  }

  function extractThroughput(data) {
    var list = null;
    if (Array.isArray(data)) list = data;
    else if (data && typeof data === 'object') {
      var keys = ['points', 'buckets', 'items', 'series', 'throughput', 'data'];
      for (var i = 0; i < keys.length; i++) {
        if (Array.isArray(data[keys[i]])) { list = data[keys[i]]; break; }
      }
    }
    if (!list) return [];
    var out = [];
    list.forEach(function (p) {
      if (isNum(p)) { out.push({ tick: null, value: p }); return; }
      if (!p || typeof p !== 'object') return;
      var val = firstNum([p.count, p.completed, p.completedTasks, p.tasks, p.value, p.throughput, p.num]);
      if (val === null) return;
      out.push({
        tick: isNum(p.tick) ? p.tick : (isNum(p.t) ? p.t : (isNum(p.bucket) ? p.bucket : null)),
        value: val
      });
    });
    return out;
  }

  /* ========================= 事件流（SSE） ========================= */

  var es = null;
  var everConnected = false;
  var reconnectDelay = 1000;
  var reconnectTimer = null;

  function connectStream() {
    if (es) { try { es.close(); } catch (e) { /* ignore */ } es = null; }
    var url = API_BASE + '/stream';
    try {
      es = new EventSource(url);
    } catch (e) {
      reportError(e, '建立事件流连接失败');
      scheduleReconnect();
      return;
    }
    es.addEventListener('open', function () {
      var reconnected = everConnected;
      everConnected = true;
      setConnected(true);
      reconnectDelay = 1000;
      if (reconnected) toast('事件流已重连，正在重新拉取全量快照', 'ok');
      refreshSnapshot().catch(function (e) { reportError(e, '重连后拉取快照失败'); });
    });
    es.addEventListener('state', function (ev) {
      var data = null;
      try { data = JSON.parse(ev.data); } catch (e) { return; }
      handleStreamMessage(data);
    });
    es.addEventListener('error', function () {
      setConnected(false);
      if (es) { try { es.close(); } catch (e) { /* ignore */ } es = null; }
      scheduleReconnect();
    });
  }

  function scheduleReconnect() {
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
    var delay = reconnectDelay;
    reconnectDelay = Math.min(reconnectDelay * 2, 8000);
    reconnectTimer = window.setTimeout(function () {
      reconnectTimer = null;
      connectStream();
    }, delay);
  }

  function setConnected(on) {
    state.connected = !!on;
    var chip = $('#top-conn');
    if (!chip) return;
    chip.textContent = on ? 'SSE 已连接' : 'SSE 未连接';
    chip.className = 'chip ' + (on ? 'on' : 'off');
  }

  function handleStreamMessage(data) {
    if (!data || typeof data !== 'object') return;
    var evs = [];
    if (Array.isArray(data.events)) evs = data.events;
    else evs = [data];
    var follow = $('#event-follow') && $('#event-follow').checked;
    evs.forEach(function (ev) {
      if (!ev || typeof ev !== 'object') return;
      if (isNum(ev.tick)) { state.sim.tick = ev.tick; dirty.top = true; }
      if (follow) {
        state.events.unshift({
          id: ev.id,
          type: ev.type || 'unknown',
          tick: ev.tick,
          aggregateId: ev.aggregateId,
          occurredAt: ev.occurredAt,
          payload: ev.payload || {}
        });
        if (state.events.length > MAX_EVENTS) state.events.length = MAX_EVENTS;
      }
    });
    if (evs.length) dirty.events = true;
    scheduleSnapshot();
  }

  /* ========================= 交互绑定 ========================= */

  function setTab(name) {
    state.activeTab = name;
    $$('.tab-btn').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-tab') === name);
    });
    $$('.tab-pane').forEach(function (p) {
      p.classList.toggle('active', p.getAttribute('data-tab') === name);
    });
    if (name === 'analysis') loadAnalytics();
    if (name === 'events') loadEventHistory();
    if (name === 'tasks') loadTaskView();
    if (name === 'agvs') { dirty.agvs = true; renderAgvDetail(); }
  }

  function loadEventHistory() {
    api('/events?size=100').then(function (d) {
      var items = extractItems(d);
      var known = Object.create(null);
      state.events.forEach(function (e) { known[e.id || (e.type + '@' + e.tick)] = true; });
      items.forEach(function (e) {
        var key = e.id || (e.type + '@' + e.tick);
        if (known[key]) return;
        state.events.push({
          id: e.id, type: e.type, tick: e.tick, aggregateId: e.aggregateId,
          occurredAt: e.occurredAt, payload: e.payload || {}
        });
      });
      state.events.sort(function (a, b) { return num(b.tick, 0) - num(a.tick, 0); });
      if (state.events.length > MAX_EVENTS) state.events.length = MAX_EVENTS;
      dirty.events = true;
    }).catch(function (e) { reportError(e, '拉取历史事件失败'); });
  }

  function loadTaskView() {
    if (!taskFilterActive()) {
      state.taskView = state.tasks.slice();
      dirty.tasks = true;
      return Promise.resolve();
    }
    var q = [];
    var st = $('#task-status').value;
    var agv = $('#task-agv').value.trim();
    var pmin = $('#task-pmin').value;
    var pmax = $('#task-pmax').value;
    if (st) q.push('status=' + encodeURIComponent(st));
    if (agv) q.push('agvId=' + encodeURIComponent(agv));
    if (pmin) q.push('priorityMin=' + encodeURIComponent(pmin));
    if (pmax) q.push('priorityMax=' + encodeURIComponent(pmax));
    q.push('size=200');
    return api('/tasks?' + q.join('&')).then(function (d) {
      state.taskView = extractItems(d);
      dirty.tasks = true;
    }).catch(function (e) {
      reportError(e, '按条件查询任务失败');
      state.taskView = state.tasks.filter(function (t) {
        if (st && t.status !== st) return false;
        if (agv && t.assignedAgvId !== agv) return false;
        if (pmin && num(t.priority, 0) < Number(pmin)) return false;
        if (pmax && num(t.priority, 0) > Number(pmax)) return false;
        return true;
      });
      dirty.tasks = true;
    });
  }

  function bindUi() {
    // 标签页
    $$('.tab-btn').forEach(function (b) {
      b.addEventListener('click', function () { setTab(b.getAttribute('data-tab')); });
    });

    // 仿真控制
    $('#btn-start').addEventListener('click', function () {
      simPost('/sim/start', null, '已启动仿真');
    });
    $('#btn-pause').addEventListener('click', function () {
      simPost('/sim/pause', null, '已暂停仿真');
    });
    $('#btn-step').addEventListener('click', function () {
      simPost('/sim/step', { ticks: 1 }, '已推进 1 个 tick');
    });
    $$('#speed-seg .seg-btn').forEach(function (b) {
      b.addEventListener('click', function () {
        var sp = Number(b.getAttribute('data-speed'));
        simPost('/sim/speed', { speed: sp }, '仿真速度已切换为 ' + sp + 'x');
      });
    });

    // 重置
    var pop = $('#reset-pop');
    $('#btn-reset').addEventListener('click', function () {
      if (pop) pop.hidden = !pop.hidden;
    });
    $('#btn-reset-cancel').addEventListener('click', function () { if (pop) pop.hidden = true; });
    $('#btn-reset-confirm').addEventListener('click', function () {
      var scenarioId = $('#reset-scenario').value || state.sim.scenarioId || '';
      var seedRaw = $('#reset-seed').value.trim();
      var body = { scenarioId: scenarioId };
      if (seedRaw !== '') {
        var seed = parseInt(seedRaw, 10);
        if (isNaN(seed)) { toast('Seed 必须是整数', 'error'); return; }
        body.seed = seed;
      }
      api('/sim/reset', { method: 'POST', body: body }).then(function () {
        toast('仿真已重置', 'ok');
        if (pop) pop.hidden = true;
        state.events = [];
        state.selectedAgvId = null;
        state.agvDetail = null;
        return Promise.all([refreshSnapshot(), loadMap()]).catch(function (e) {
          reportError(e, '重置后刷新数据失败');
        });
      }).catch(function (e) { reportError(e, '重置仿真失败'); });
    });

    // 事件注入
    $('#inject-type').addEventListener('change', renderInjectFields);
    renderInjectFields();
    $('#btn-inject').addEventListener('click', injectEvent);

    // 订单
    $('#ord-add-line').addEventListener('click', function () { addLineRow(); });
    $('#ord-create').addEventListener('click', createOrder);
    $('#order-refresh').addEventListener('click', function () {
      loadOrderView().catch(function (e) { reportError(e, '刷新订单列表失败'); });
    });
    $('#order-status').addEventListener('change', function () {
      loadOrderView().catch(function (e) { reportError(e, '按状态查询订单失败'); });
    });
    $('#orders-table').addEventListener('click', function (ev) {
      var btn = ev.target.closest ? ev.target.closest('[data-act]') : null;
      if (!btn) return;
      var id = btn.getAttribute('data-id');
      var act = btn.getAttribute('data-act');
      if (act === 'order-release') {
        api('/orders/' + encodeURIComponent(id) + '/release', { method: 'POST' })
          .then(function () { toast('订单已释放并生成任务', 'ok'); return refreshSnapshot(); })
          .catch(function (e) { reportError(e, '释放订单失败'); });
      } else if (act === 'order-cancel') {
        api('/orders/' + encodeURIComponent(id) + '/cancel', { method: 'POST' })
          .then(function () { toast('订单已取消', 'ok'); return refreshSnapshot(); })
          .catch(function (e) { reportError(e, '取消订单失败'); });
      }
    });

    // 任务
    $('#task-query').addEventListener('click', function () {
      loadTaskView().catch(function (e) { reportError(e, '查询任务失败'); });
    });
    $('#task-reset').addEventListener('click', function () {
      $('#task-status').value = ''; $('#task-agv').value = ''; $('#task-pmin').value = ''; $('#task-pmax').value = '';
      loadTaskView();
    });
    $('#tasks-table').addEventListener('click', function (ev) {
      var btn = ev.target.closest ? ev.target.closest('[data-act]') : null;
      if (!btn) return;
      var id = btn.getAttribute('data-id');
      var act = btn.getAttribute('data-act');
      if (act === 'task-prio') {
        var input = $('#tasks-table [data-prio="' + id + '"]');
        var p = parseInt(input ? input.value : '', 10);
        if (isNaN(p) || p < 1 || p > 10) { toast('优先级必须是 1-10 的整数', 'error'); return; }
        api('/tasks/' + encodeURIComponent(id) + '/priority', { method: 'POST', body: { priority: p } })
          .then(function () { toast('任务优先级已调整为 ' + p, 'ok'); return refreshSnapshot(); })
          .catch(function (e) { reportError(e, '调整任务优先级失败'); });
      } else if (act === 'task-cancel') {
        api('/tasks/' + encodeURIComponent(id) + '/cancel', { method: 'POST' })
          .then(function () { toast('任务已取消', 'ok'); return refreshSnapshot(); })
          .catch(function (e) { reportError(e, '取消任务失败'); });
      } else if (act === 'task-reassign') {
        api('/tasks/' + encodeURIComponent(id) + '/reassign', { method: 'POST' })
          .then(function () { toast('任务已重派回任务池', 'ok'); return refreshSnapshot(); })
          .catch(function (e) { reportError(e, '重派任务失败'); });
      } else if (act === 'task-timeline') {
        loadTaskTimeline(id);
      }
    });

    // AGV 列表
    $('#agv-refresh').addEventListener('click', function () {
      api('/agvs').then(function (d) {
        var items = extractItems(d);
        if (items.length) state.agvs = items;
        syncAgvRender();
        dirty.agvs = true;
      }).catch(function (e) { reportError(e, '刷新 AGV 列表失败'); });
    });
    $('#agv-status').addEventListener('change', function () { dirty.agvs = true; });
    $('#agv-list').addEventListener('click', function (ev) {
      var item = ev.target.closest ? ev.target.closest('[data-act="focus-agv"]') : null;
      if (!item) return;
      selectAgv(item.getAttribute('data-id'), true);
    });

    // 封路解除
    $('#roadblock-list').addEventListener('click', function (ev) {
      var btn = ev.target.closest ? ev.target.closest('[data-act="unblock"]') : null;
      if (!btn) return;
      api('/map/roadblocks/' + encodeURIComponent(btn.getAttribute('data-id')), { method: 'DELETE' })
        .then(function () {
          toast('已解除封路', 'ok');
          return Promise.all([loadMap(), refreshSnapshot()]);
        })
        .catch(function (e) { reportError(e, '解除封路失败'); });
    });

    // 事件面板
    $('#event-refresh').addEventListener('click', loadEventHistory);
    $('#event-filter').addEventListener('input', function () { dirty.events = true; });

    bindMapEvents();
    window.addEventListener('resize', function () { dirty.analysis = true; });
  }

  function simPost(path, body, okMsg) {
    api(path, { method: 'POST', body: body || undefined })
      .then(function () {
        toast(okMsg, 'ok');
        return refreshSnapshot();
      })
      .catch(function (e) { reportError(e, '仿真控制操作失败'); });
  }

  function loadOrderView() {
    var st = $('#order-status').value;
    if (!st) {
      state.orderView = state.orders.slice();
      dirty.orders = true;
      return Promise.resolve();
    }
    return api('/orders?status=' + encodeURIComponent(st) + '&size=200').then(function (d) {
      state.orderView = extractItems(d);
      dirty.orders = true;
    }).catch(function (e) {
      reportError(e, '按状态查询订单失败');
      state.orderView = state.orders.filter(function (o) { return o.status === st; });
      dirty.orders = true;
    });
  }

  /* ---------- 订单明细行 ---------- */

  function addLineRow(skuCode, qty) {
    var box = $('#ord-lines');
    if (!box) return;
    var row = document.createElement('div');
    row.className = 'line-row';
    row.innerHTML =
      '<input class="inp" type="text" list="dl-sku" placeholder="SKU 编码" data-line-sku value="' + esc(skuCode || '') + '" />' +
      '<input class="inp" type="number" min="1" step="1" placeholder="数量" data-line-qty value="' + esc(qty === undefined ? 1 : qty) + '" />' +
      '<button class="btn tiny" type="button" data-act="remove-line">×</button>';
    box.appendChild(row);
    row.querySelector('[data-act="remove-line"]').addEventListener('click', function () {
      box.removeChild(row);
    });
  }

  function collectLines() {
    var rows = $$('#ord-lines .line-row');
    var lines = [];
    rows.forEach(function (r) {
      var skuEl = r.querySelector('[data-line-sku]');
      var qtyEl = r.querySelector('[data-line-qty]');
      var sku = skuEl ? skuEl.value.trim() : '';
      var qty = qtyEl ? parseInt(qtyEl.value, 10) : NaN;
      if (!sku) return;
      if (isNaN(qty) || qty <= 0) { toast('明细数量必须是大于 0 的整数', 'error'); return; }
      lines.push({ skuCode: sku, qty: qty });
    });
    return lines;
  }

  function createOrder() {
    var code = $('#ord-code').value.trim();
    var drop = $('#ord-drop').value.trim();
    var priority = clamp(parseInt($('#ord-priority').value, 10) || 5, 1, 10);
    var requester = $('#ord-requester').value.trim() || 'demo';
    if (!code) { toast('请填写订单单号', 'error'); return; }
    if (!drop) { toast('请填写放货库位编码', 'error'); return; }
    var lines = collectLines();
    if (!lines.length) { toast('请至少填写一条有效明细（SKU 编码 + 数量）', 'error'); return; }
    api('/orders', {
      method: 'POST',
      body: { code: code, priority: priority, requester: requester, dropLocationCode: drop, lines: lines }
    }).then(function () {
      toast('订单创建成功：' + code, 'ok');
      $('#ord-code').value = '';
      $('#ord-lines').innerHTML = '';
      addLineRow();
      return refreshSnapshot();
    }).catch(function (e) { reportError(e, '创建订单失败'); });
  }

  /* ---------- 事件注入 ---------- */

  function renderInjectFields() {
    var box = $('#inject-fields');
    if (!box) return;
    var type = $('#inject-type').value;
    if (type === 'fault') {
      box.innerHTML =
        '<input class="inp" style="width:130px" id="inj-target" list="dl-agv" placeholder="AGV 编号，如 AGV-01" />' +
        '<input class="inp" style="width:110px" id="inj-code" placeholder="故障码，如 E-SENSOR" />';
    } else if (type === 'offline') {
      box.innerHTML = '<input class="inp" style="width:130px" id="inj-target" list="dl-agv" placeholder="AGV 编号，如 AGV-01" />';
    } else if (type === 'recover') {
      box.innerHTML = '<input class="inp" style="width:130px" id="inj-target" list="dl-agv" placeholder="AGV 编号，如 AGV-01" />';
    } else if (type === 'roadblock') {
      box.innerHTML = '<input class="inp" style="width:170px" id="inj-cells" placeholder="格点，如 12,7（多个用空格分隔）" />';
    } else if (type === 'rush_order') {
      box.innerHTML =
        '<input class="inp" style="width:110px" id="inj-sku" list="dl-sku" placeholder="SKU 编码" />' +
        '<input class="inp" style="width:70px" id="inj-qty" type="number" min="1" value="1" placeholder="数量" />' +
        '<input class="inp" style="width:70px" id="inj-priority" type="number" min="1" max="10" value="10" placeholder="优先级" />';
    }
  }

  function injectEvent() {
    var type = $('#inject-type').value;
    var body = { type: type };
    if (type === 'fault') {
      var target = ($('#inj-target') ? $('#inj-target').value : '').trim();
      var code = ($('#inj-code') ? $('#inj-code').value : '').trim();
      if (!target) { toast('请填写要注入故障的 AGV 编号', 'error'); return; }
      body.target = target;
      body.code = code || 'E-UNKNOWN';
    } else if (type === 'offline' || type === 'recover') {
      var t2 = ($('#inj-target') ? $('#inj-target').value : '').trim();
      if (!t2) { toast('请填写 AGV 编号', 'error'); return; }
      body.target = t2;
    } else if (type === 'roadblock') {
      var raw = ($('#inj-cells') ? $('#inj-cells').value : '').trim();
      if (!raw) { toast('请填写要封路的格点，如 12,7', 'error'); return; }
      // 形如 "12,7" 的格点需要按逗号拆分成 x,y
      var parts = raw.split(/[\s;]+/).filter(function (s) { return s.length; });
      var parsed = [];
      parts.forEach(function (p) {
        var m = p.match(/^(\d+)\s*,\s*(\d+)$/);
        if (m) parsed.push(m[1] + ',' + m[2]);
        else {
          toast('格点格式不正确：' + p + '（应为 x,y，如 12,7）', 'error');
          parsed = null;
        }
      });
      if (!parsed || !parsed.length) return;
      body.cells = parsed;
    } else if (type === 'rush_order') {
      var sku = ($('#inj-sku') ? $('#inj-sku').value : '').trim();
      var qty = parseInt(($('#inj-qty') ? $('#inj-qty').value : '1'), 10);
      var pr = parseInt(($('#inj-priority') ? $('#inj-priority').value : '10'), 10);
      if (!sku) { toast('请填写急单 SKU 编码', 'error'); return; }
      if (isNaN(qty) || qty <= 0) { toast('急单数量必须是大于 0 的整数', 'error'); return; }
      body.skuCode = sku;
      body.qty = qty;
      body.priority = isNaN(pr) ? 10 : clamp(pr, 1, 10);
    }

    api('/sim/inject', { method: 'POST', body: body }).then(function () {
      toast('事件注入成功：' + type, 'ok');
      var after = refreshSnapshot();
      if (type === 'roadblock') after = Promise.all([after, loadMap()]);
      return after;
    }).catch(function (e) { reportError(e, '注入事件失败'); });
  }

  /* ---------- 任务时间线 ---------- */

  function loadTaskTimeline(id) {
    api('/analytics/tasks/' + encodeURIComponent(id) + '/timeline').then(function (d) {
      var card = $('#timeline-card'), body = $('#timeline-body');
      if (!card || !body) return;
      card.hidden = false;
      var list = Array.isArray(d) ? d
        : (d && Array.isArray(d.events) ? d.events
          : (d && Array.isArray(d.timeline) ? d.timeline
            : (d && Array.isArray(d.points) ? d.points : null)));
      if (!list) {
        body.innerHTML = '<div class="muted">后端返回的任务时间线：</div><pre class="payload" style="white-space:pre-wrap;overflow-wrap:anywhere">' +
          esc(JSON.stringify(d, null, 2)) + '</pre>';
        return;
      }
      body.innerHTML = '<div class="table-wrap"><table class="tbl"><thead><tr><th>tick</th><th>类型</th><th>说明</th></tr></thead><tbody>' +
        list.map(function (p) {
          if (typeof p !== 'object' || p === null) return '<tr><td colspan="3">' + esc(p) + '</td></tr>';
          var t = isNum(p.tick) ? p.tick : (isNum(p.t) ? p.t : '--');
          var ty = p.type || p.event || p.name || '--';
          var desc = p.message || p.description || p.payload || '';
          if (typeof desc === 'object') { try { desc = JSON.stringify(desc); } catch (e) { desc = ''; } }
          return '<tr><td>' + esc(t) + '</td><td>' + esc(ty) + '</td><td class="payload">' + esc(desc) + '</td></tr>';
        }).join('') + '</tbody></table></div>';
    }).catch(function (e) {
      reportError(e, '加载任务时间线失败');
      var card = $('#timeline-card'), body = $('#timeline-body');
      if (card && body) {
        card.hidden = false;
        body.innerHTML = '<div class="muted">任务时间线加载失败。</div>';
      }
    });
  }

  /* ========================= 渲染循环 ========================= */

  var frames = 0, fpsMark = 0;

  function loop(now) {
    if (!fpsMark) fpsMark = now;
    frames++;
    if (now - fpsMark >= 500) {
      var fps = Math.round(frames * 1000 / (now - fpsMark));
      var el = $('#fps');
      if (el) el.textContent = String(fps);
      frames = 0; fpsMark = now;
    }

    try {
      drawFrame(now);
    } catch (e) {
      reportError(e, '地图绘制异常');
    }

    if (dirty.top) { renderTop(); dirty.top = false; }
    if (dirty.map) { dirty.map = false; }
    if (dirty.roadblock) { renderRoadblocks(); dirty.roadblock = false; }
    if (dirty.orders && state.activeTab === 'orders') { renderOrders(); dirty.orders = false; }
    if (dirty.tasks && state.activeTab === 'tasks') { renderTasks(); dirty.tasks = false; }
    if (dirty.agvs && state.activeTab === 'agvs') { renderAgvs(); dirty.agvs = false; }
    if (dirty.analysis && state.activeTab === 'analysis') { renderAnalysis(); dirty.analysis = false; }
    if (dirty.events && state.activeTab === 'events') { renderEvents(); dirty.events = false; }

    window.requestAnimationFrame(loop);
  }

  /* ========================= 初始化 ========================= */

  function init() {
    bindUi();
    addLineRow();

    api('/health').then(function (h) {
      if (h && isNum(h.tick)) state.sim.tick = h.tick;
      if (h && typeof h.running === 'boolean') state.sim.running = h.running;
      if (h && h.seed !== undefined) state.sim.seed = h.seed;
      if (h && h.version) state.meta.version = h.version;
      dirty.top = true;
    }).catch(function (e) { reportError(e, '后端健康检查失败'); });

    api('/meta').then(function (m) {
      if (m) {
        state.meta.width = num(m.width, state.meta.width);
        state.meta.height = num(m.height, state.meta.height);
        state.meta.tickSeconds = num(m.tickSeconds, 1);
        state.meta.mapId = m.mapId || state.meta.mapId;
        state.meta.mapVersion = m.mapVersion || state.meta.mapVersion;
        if (m.seed !== undefined && m.seed !== null) state.meta.seed = m.seed;
      }
      dirty.top = true;
      dirty.map = true;
    }).catch(function (e) { reportError(e, '加载地图元信息失败'); });

    loadMap().catch(function (e) { reportError(e, '加载地图失败'); });
    refreshSnapshot().catch(function (e) { reportError(e, '加载仿真快照失败'); });
    loadReference();

    connectStream();
    window.setInterval(function () {
      if (!state.connected) {
        refreshSnapshot().catch(function (e) { reportError(e, '兜底轮询失败'); });
      }
    }, FALLBACK_POLL_MS);
    window.setInterval(function () {
      if (state.activeTab === 'analysis') loadAnalytics();
    }, ANALYTICS_REFRESH_MS);

    window.requestAnimationFrame(loop);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
