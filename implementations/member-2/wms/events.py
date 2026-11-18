"""事件总线。

事件是系统对外的事实输出：既写入数据库供查询与统计，也通过订阅推送给
WebSocket 端。事件结构遵循公共契约 `docs/data-schema.md` 第 11 节。
"""

from __future__ import annotations

import queue
import threading

from .util import dumps, new_id, now_iso


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._seq = 0
        self._recent: list[dict] = []

    # ------------------------------------------------------------ 序号
    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def set_seq(self, value: int) -> None:
        with self._lock:
            self._seq = max(self._seq, int(value))

    # ------------------------------------------------------------ 订阅
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # ------------------------------------------------------------ 发布
    def publish(self, event_type: str, aggregate_id: str | None, payload: dict, tick: int, occurred_at: str | None = None) -> dict:
        event = {
            "id": new_id("evt"),
            "type": event_type,
            "tick": int(tick),
            "aggregateId": aggregate_id,
            "payload": payload or {},
            "occurredAt": occurred_at or now_iso(),
            "seq": self.next_seq(),
        }
        with self._lock:
            self._recent.append(event)
            if len(self._recent) > 500:
                self._recent.pop(0)
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                # 订阅端消费不及时：丢弃该条增量，客户端重连后会重新拉快照
                pass
        return event

    def recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._recent[-limit:])
