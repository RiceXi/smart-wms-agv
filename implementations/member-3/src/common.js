import { createHash } from 'node:crypto';

export class DomainError extends Error {
  constructor(code, message, status = 400, details = {}) {
    super(message);
    Object.assign(this, { code, status, details });
  }
}

export function requireValue(condition, code, message, details) {
  if (!condition) throw new DomainError(code, message, 400, details);
}

export function integer(value, name, min = 0, max = Number.MAX_SAFE_INTEGER) {
  requireValue(Number.isSafeInteger(value) && value >= min && value <= max,
    'invalid_number', `${name} 必须为 ${min}～${max} 范围内的整数`);
  return value;
}

export function textValue(value, name, max = 120) {
  requireValue(typeof value === 'string' && value.trim().length > 0 && value.trim().length <= max,
    'invalid_text', `${name} 不能为空，且长度不能超过 ${max}`);
  return value.trim();
}

export function enumValue(value, values, name) {
  requireValue(values.includes(value), 'invalid_enum', `${name} 只允许：${values.join(', ')}`);
  return value;
}

export function isoDate(value) {
  if (value === undefined || value === null || value === '') return null;
  requireValue(typeof value === 'string' && /^\d{4}-\d{2}-\d{2}(T.*Z)?$/.test(value), 'invalid_date', '日期必须为 YYYY-MM-DD 或 UTC ISO 8601');
  const parsed = new Date(value);
  requireValue(Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value.slice(0, 10), 'invalid_date', '日期不存在');
  return parsed.toISOString();
}

export function deterministicId(seed, sequence) {
  const hex = createHash('sha256').update(`${seed}:${sequence}`).digest('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-a${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

export const cell = point => `${point.x},${point.y}`;
export const distance = (left, right) => Math.abs(left.x - right.x) + Math.abs(left.y - right.y);
export const clone = value => structuredClone(value);

export function cyclesOf(graph) {
  const visited = new Set();
  const active = [];
  const cycles = [];
  function visit(node) {
    const index = active.indexOf(node);
    if (index >= 0) { cycles.push(active.slice(index)); return; }
    if (visited.has(node)) return;
    visited.add(node);
    active.push(node);
    for (const next of graph[node] ?? []) visit(next);
    active.pop();
  }
  for (const node of Object.keys(graph).sort()) visit(node);
  return cycles;
}
