"""核销门禁：必须处于 claimed，且设有目标时累计必须达标。

未达标返回 target_not_reached，调用方必须保持 claimed 不变。
"""
from app.modules.progress import project


def can_fulfill(status: str, target, contributed) -> dict:
    if status != "claimed":
        return {"ok": False, "reason": "need_claim"}
    if target is not None and project(contributed, target)["funded"] is False:
        return {"ok": False, "reason": "target_not_reached"}
    return {"ok": True, "reason": ""}
