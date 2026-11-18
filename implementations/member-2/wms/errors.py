"""统一错误类型。

错误码与公共契约 `docs/acceptance.md` 第 20 条一致：
所有失败都必须是可理解、可定位的结构化错误，而不是 500 堆栈。
"""

from __future__ import annotations


class AppError(Exception):
    """业务异常基类，携带错误码、提示文案和细节。"""

    status = 400
    code = "VALIDATION_FAILED"

    def __init__(self, message: str, details: dict | None = None, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code
        if status:
            self.status = status

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class ValidationError(AppError):
    status = 400
    code = "VALIDATION_FAILED"


class NotFoundError(AppError):
    status = 404
    code = "NOT_FOUND"


class DuplicateCodeError(AppError):
    status = 409
    code = "DUPLICATE_CODE"


class InvalidStateError(AppError):
    """状态机不允许当前操作。"""

    status = 409
    code = "INVALID_STATE"


class ConflictError(AppError):
    """资源被占用（库存被引用、充电位满、路径冲突）。"""

    status = 409
    code = "CONFLICT"


class InsufficientInventoryError(AppError):
    status = 422
    code = "INSUFFICIENT_INVENTORY"
