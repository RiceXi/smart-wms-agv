import { cell, distance, integer, requireValue } from './common.js';
import { validatePath } from './map-engine.js';

class MinHeap {
  values = [];
  push(item) {
    this.values.push(item);
    let index = this.values.length - 1;
    while (index > 0) {
      const parent = (index - 1) >> 1;
      if (this.compare(this.values[parent], item) <= 0) break;
      this.values[index] = this.values[parent];
      index = parent;
    }
    this.values[index] = item;
  }
  compare(left, right) { return left.score - right.score || left.heuristic - right.heuristic || left.sequence - right.sequence; }
  pop() {
    const first = this.values[0];
    const last = this.values.pop();
    if (this.values.length) {
      let index = 0;
      while (index * 2 + 1 < this.values.length) {
        let child = index * 2 + 1;
        if (child + 1 < this.values.length && this.compare(this.values[child + 1], this.values[child]) < 0) child++;
        if (this.compare(last, this.values[child]) <= 0) break;
        this.values[index] = this.values[child];
        index = child;
      }
      this.values[index] = last;
    }
    return first;
  }
}

export class ReservationTable {
  constructor(map) { this.map = map; this.vertices = new Map(); this.edges = new Map(); }
  vertexKey(point) { return `vertex:${this.map.id}:${cell(point)}:${point.tick}`; }
  edgeKey(from, to, tick) { return `edge:${this.map.id}:${cell(from)}:${cell(to)}:${tick}`; }
  conflict(agvId, from, to) {
    const vertex = this.vertices.get(this.vertexKey(to));
    if (vertex && vertex.agvId !== agvId) return { reason: 'vertex_conflict', blocker: vertex.agvId, tick: to.tick, cell: cell(to) };
    if (from) {
      const reverse = this.edges.get(this.edgeKey(to, from, from.tick));
      if (reverse && reverse.agvId !== agvId) return { reason: 'opposite_edge', blocker: reverse.agvId, tick: from.tick, cell: cell(to) };
    }
    return null;
  }
  reserve(agvId, planVersion, ticks, holdUntil = ticks.at(-1)?.tick) {
    validatePath(this.map, ticks);
    integer(planVersion, 'planVersion', 0);
    integer(holdUntil, '预约截止 tick', ticks.at(-1).tick);
    const points = [...ticks];
    const finalPoint = ticks.at(-1);
    for (let tick = finalPoint.tick + 1; tick <= holdUntil; tick++) points.push({ ...finalPoint, tick, action: 'wait' });
    for (let index = 0; index < points.length; index++) {
      const conflict = this.conflict(agvId, points[index - 1], points[index]);
      if (conflict) return { ok: false, ...conflict };
    }
    this.release(agvId);
    const owner = { agvId, planVersion, expiresAt: holdUntil + 2 };
    for (let index = 0; index < points.length; index++) {
      this.vertices.set(this.vertexKey(points[index]), owner);
      if (index) this.edges.set(this.edgeKey(points[index - 1], points[index], points[index - 1].tick), owner);
    }
    return { ok: true };
  }
  release(agvId) {
    for (const table of [this.vertices, this.edges]) for (const [key, value] of table) if (value.agvId === agvId) table.delete(key);
  }
  expire(tick) {
    for (const table of [this.vertices, this.edges]) for (const [key, value] of table) {
      if (Number(key.split(':').at(-1)) < tick || value.expiresAt < tick) table.delete(key);
    }
  }
  entries() { return [...this.vertices, ...this.edges].map(([key, owner]) => ({ key, value: `${owner.agvId}:${owner.planVersion}`, ...owner })); }
}

export function findPath(map, reservations, agvId, start, goal, startTick, options = {}) {
  const horizon = options.horizon ?? 160;
  const holdTicks = options.holdTicks ?? 0;
  integer(startTick, '开始 tick');
  const walkable = new Set(map.nodes.filter(node => node.walkable).map(cell));
  requireValue(walkable.has(cell(start)) && walkable.has(cell(goal)), 'blocked_path', '路径起点和终点必须可通行');
  const adjacency = new Map();
  for (const edge of map.edges) {
    if (!adjacency.has(edge.from)) adjacency.set(edge.from, []);
    adjacency.get(edge.from).push(edge.to);
    if (edge.bidirectional) {
      if (!adjacency.has(edge.to)) adjacency.set(edge.to, []);
      adjacency.get(edge.to).push(edge.from);
    }
  }
  const first = { ...start, tick: startTick, parent: null, heuristic: distance(start, goal), sequence: 0 };
  first.score = first.heuristic;
  const queue = new MinHeap();
  queue.push(first);
  const seen = new Set([`${cell(start)},${startTick}`]);
  const blockers = new Set();
  let firstConflict = null;
  let sequence = 0;
  let expanded = 0;
  while (queue.values.length && expanded < 30000) {
    const current = queue.pop();
    expanded++;
    if (cell(current) === cell(goal)) {
      let safe = true;
      for (let offset = 1; offset <= holdTicks; offset++) {
        const conflict = reservations.conflict(agvId, null, { ...current, tick: current.tick + offset });
        if (conflict) { safe = false; blockers.add(conflict.blocker); firstConflict ??= conflict; break; }
      }
      if (safe) {
        const path = [];
        let cursor = current;
        while (cursor) {
          path.push({ x: cursor.x, y: cursor.y, tick: cursor.tick, action: cursor.parent && cell(cursor.parent) === cell(cursor) ? 'wait' : 'move' });
          cursor = cursor.parent;
        }
        return { ok: true, ticks: path.reverse(), blockers: [...blockers], expanded };
      }
    }
    if (current.tick - startTick >= horizon) continue;
    const nextCells = [...(adjacency.get(cell(current)) ?? []), cell(current)];
    for (const target of nextCells) {
      const [x, y] = target.split(',').map(Number);
      const next = { x, y, tick: current.tick + 1 };
      const key = `${target},${next.tick}`;
      if (seen.has(key)) continue;
      const conflict = reservations.conflict(agvId, current, next);
      if (conflict) { blockers.add(conflict.blocker); firstConflict ??= conflict; continue; }
      seen.add(key);
      const heuristic = distance(next, goal);
      queue.push({ ...next, parent: current, heuristic, score: next.tick - startTick + heuristic, sequence: ++sequence });
    }
  }
  return { ok: false, reason: firstConflict?.reason ?? 'no_path', blockers: [...blockers], expanded };
}
