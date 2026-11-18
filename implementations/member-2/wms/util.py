"""通用工具：ID、时间、校验、JSON 辅助。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .errors import ValidationError


def new_id(prefix: str = "") -> str:
    """生成 UUID 字符串；prefix 仅用于人读，不参与唯一性。"""
    raw = uuid.uuid4().hex
    return f"{prefix}-{raw}" if prefix else raw


def now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串，毫秒精度，带 Z 后缀。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def cell_id(x: int, y: int) -> str:
    """格点标识格式：`x,y`。与公共契约示例一致。"""
    return f"{x},{y}"


def parse_cell_id(value: str) -> tuple[int, int]:
    """解析 `x,y`，失败抛 ValidationError。"""
    if not isinstance(value, str) or "," not in value:
        raise ValidationError("格点 id 必须是 `x,y` 格式", {"value": value})
    left, _, right = value.partition(",")
    try:
        return int(left), int(right)
    except ValueError as exc:  # pragma: no cover - 防御分支
        raise ValidationError("格点 id 的坐标必须是整数", {"value": value}) from exc


def require(body: dict, field: str, kind: type | tuple[type, ...] | None = None, default=None, required: bool = True):
    """读取并校验字段，缺失或类型不符时抛 ValidationError。"""
    if field not in body or body[field] is None:
        if required and default is None:
            raise ValidationError(f"缺少必填字段 {field}", {"field": field})
        return default
    value = body[field]
    if kind is not None and not isinstance(value, kind):
        # bool 是 int 的子类，需要单独排除
        if isinstance(value, bool) or not isinstance(value, kind):
            raise ValidationError(
                f"字段 {field} 类型不正确，应为 {getattr(kind, '__name__', kind)}",
                {"field": field, "value": value},
            )
    return value


def require_str(body: dict, field: str, max_len: int = 128, min_len: int = 1, required: bool = True, default: str = ""):
    value = require(body, field, str, default=default, required=required)
    if value is None:
        return None
    value = value.strip()
    if not value:
        # 可选字段允许为空；必填字段空值视为缺失
        if not required:
            return ""
        raise ValidationError(f"字段 {field} 不能为空", {"field": field})
    if len(value) < min_len:
        raise ValidationError(f"字段 {field} 长度不能小于 {min_len}", {"field": field})
    if len(value) > max_len:
        raise ValidationError(f"字段 {field} 长度不能大于 {max_len}", {"field": field})
    return value


def require_int(body: dict, field: str, minimum: int | None = None, maximum: int | None = None, required: bool = True, default=None):
    value = require(body, field, int, default=default, required=required)
    if value is None:
        return None
    if minimum is not None and value < minimum:
        raise ValidationError(f"字段 {field} 不能小于 {minimum}", {"field": field, "value": value})
    if maximum is not None and value > maximum:
        raise ValidationError(f"字段 {field} 不能大于 {maximum}", {"field": field, "value": value})
    return value


def require_number(body: dict, field: str, minimum: float | None = None, maximum: float | None = None, required: bool = True, default=None):
    value = require(body, field, (int, float), default=default, required=required)
    if value is None:
        return None
    value = float(value)
    if minimum is not None and value < minimum:
        raise ValidationError(f"字段 {field} 不能小于 {minimum}", {"field": field, "value": value})
    if maximum is not None and value > maximum:
        raise ValidationError(f"字段 {field} 不能大于 {maximum}", {"field": field, "value": value})
    return value


def require_enum(body: dict, field: str, allowed, required: bool = True, default=None):
    value = require(body, field, str, default=default, required=required)
    if value is None:
        return None
    if value not in allowed:
        raise ValidationError(
            f"字段 {field} 取值非法",
            {"field": field, "value": value, "allowed": list(allowed)},
        )
    return value


def round2(value: float) -> float:
    """统一保留两位小数，避免浮点噪声进入事件与统计。"""
    return round(value + 0.0, 2)


def row_to_camel(row) -> dict:
    """把数据库行（snake_case 列名）转换成对外的 camelCase 结构。

    只转换键名，不改值；已经是 camelCase 的键原样保留。
    """
    payload: dict = {}
    for key, value in dict(row).items():
        parts = str(key).split("_")
        camel = parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
        payload[camel] = value
    return payload


def dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(text: str | None, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
