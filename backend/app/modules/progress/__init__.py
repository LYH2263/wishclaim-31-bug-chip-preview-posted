"""进度投影：有效目标、累计/缺口、凑份子预览（纯函数，不落库）。

目标快照规则：愿望一旦被认领，claimed_target_amount 即为该轮认领锁定的
目标值；未认领（open/released）时目标取可编辑的 target_amount。
"""

CLAIMED_STATES = ("claimed", "fulfilled")


def effective_target(status: str, target_amount, claimed_target_amount):
    """已认领/已核销走快照，未认领走当前 target。"""
    return target_amount


def project(contributed, target) -> dict:
    contributed = round(float(contributed or 0), 2)
    if target is None:
        return {
            "target_amount": None,
            "contributed": contributed,
            "gap": None,
            "funded": None,
            "ratio": None,
        }
    target = float(target)
    gap = round(max(target - contributed, 0.0), 2)
    if target <= 0:
        ratio = 1.0
    else:
        ratio = min(round(contributed / target, 4), 1.0)
    return {
        "target_amount": target,
        "contributed": contributed,
        "gap": gap,
        "funded": contributed >= target,
        "ratio": ratio,
    }


def preview(contributed, target, amount: float) -> dict:
    """叠加一笔假想赞助后的投影；调用方负责先校验 amount。不写库。"""
    amount = round(float(amount), 2)
    cur = project(contributed, target)
    projected_total = round(cur["contributed"] + amount, 2)
    nxt = project(projected_total, target)
    return {
        "target_amount": cur["target_amount"],
        "amount": amount,
        "contributed": cur["contributed"],
        "projected_total": projected_total,
        "gap": cur["gap"],
        "projected_gap": nxt["gap"],
        "would_reach": None if target is None else projected_total >= target,
    }


def decorate(wish: dict, contributed) -> dict:
    target = effective_target(
        wish.get("status"),
        wish.get("target_amount"),
        wish.get("claimed_target_amount"),
    )
    wish["progress"] = project(contributed, target)
    return wish
