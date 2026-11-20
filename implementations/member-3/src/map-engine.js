import { cell, distance, requireValue, integer } from './common.js';

export function buildMap(config, id, version) {
  const nodes = [];
  for (let y = 0; y < config.height; y++) {
    for (let x = 0; x < config.width; x++) {
      const shelf = [5, 6, 10, 11, 15, 16].includes(x) && y >= 4 && y <= 10 && y !== 7;
      nodes.push({ x, y, type: shelf ? 'shelf' : 'floor', capacity: 1, walkable: !shelf });
    }
  }
  return rebuildEdges({ id, version, width: config.width, height: config.height, tick_seconds: 1, nodes, closable: config.closable });
}

export function rebuildEdges(map) {
  const walkable = new Set(map.nodes.filter(node => node.walkable).map(cell));
  map.edges = [];
  for (const node of map.nodes.filter(item => item.walkable)) {
    for (const target of [{ x: node.x + 1, y: node.y }, { x: node.x, y: node.y + 1 }]) {
      if (walkable.has(cell(target))) map.edges.push({ from: cell(node), to: cell(target), bidirectional: true });
    }
  }
  return map;
}

export function mapIndex(map) {
  return new Map(map.nodes.map(node => [cell(node), node]));
}

export function coordinates(map, point) {
  integer(point.x, '横坐标', 0, map.width - 1);
  integer(point.y, '纵坐标', 0, map.height - 1);
}

export function neighbors(map, point, includeWait = true) {
  const index = mapIndex(map);
  const candidates = [
    { x: point.x + 1, y: point.y }, { x: point.x, y: point.y + 1 },
    { x: point.x - 1, y: point.y }, { x: point.x, y: point.y - 1 }
  ];
  if (includeWait) candidates.push({ x: point.x, y: point.y });
  return candidates.filter(next => {
    if (!index.get(cell(next))?.walkable) return false;
    return distance(point, next) === 0 || map.edges.some(edge =>
      (edge.from === cell(point) && edge.to === cell(next)) ||
      (edge.bidirectional && edge.to === cell(point) && edge.from === cell(next)));
  });
}

export function validatePath(map, ticks) {
  requireValue(Array.isArray(ticks) && ticks.length > 0, 'invalid_path', '路径不能为空');
  const index = mapIndex(map);
  for (let position = 0; position < ticks.length; position++) {
    const point = ticks[position];
    coordinates(map, point);
    integer(point.tick, 'tick');
    requireValue(index.get(cell(point))?.walkable, 'blocked_path', '路径不能进入货架或封路格');
    if (position) {
      const previous = ticks[position - 1];
      requireValue(point.tick === previous.tick + 1 && distance(point, previous) <= 1,
        'discontinuous_path', '路径必须每 tick 停留或四邻移动一格');
      requireValue(neighbors(map, previous).some(next => cell(next) === cell(point)), 'invalid_edge', '路径不能违反有向边约束');
    }
  }
  return true;
}
