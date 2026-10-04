"""赞助账本（chip-in ledger）。

与认领锁（claimer 轨）完全分离：这里只认 sponsor 名字与金额，
同一人既认领又赞助不受限制，由两条轨道各自记录。
"""
import math

DDL = """
CREATE TABLE IF NOT EXISTS chip_ins(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  wish_id INTEGER NOT NULL,
  sponsor TEXT NOT NULL,
  amount REAL NOT NULL,
  created_at TEXT NOT NULL
)
"""


def create_table(c):
    c.execute(DDL)


def validate_amount(amount) -> dict:
    """单笔必须是有限正数；<=0 / NaN / inf 一律拒。"""
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return {"ok": False, "reason": "invalid_amount"}
    if not math.isfinite(float(amount)):
        return {"ok": False, "reason": "invalid_amount"}
    if amount <= 0:
        return {"ok": False, "reason": "amount_not_positive"}
    return {"ok": True, "reason": ""}


def normalize_sponsor(sponsor: str) -> str | None:
    if not isinstance(sponsor, str):
        return None
    s = sponsor.strip()
    return s or None


def add(c, wish_id: int, sponsor: str, amount: float, at: str) -> int:
    cur = c.execute(
        "INSERT INTO chip_ins(wish_id,sponsor,amount,created_at) VALUES (?,?,?,?)",
        (wish_id, sponsor, round(float(amount), 2), at),
    )
    return cur.lastrowid


def total(c, wish_id: int) -> float:
    r = c.execute(
        "SELECT COALESCE(SUM(amount),0) AS s FROM chip_ins WHERE wish_id=?",
        (wish_id,),
    ).fetchone()
    return round(float(r["s"] or 0), 2)


def totals_map(c) -> dict:
    return {
        r["wish_id"]: round(float(r["s"]), 2)
        for r in c.execute(
            "SELECT wish_id, SUM(amount) AS s FROM chip_ins GROUP BY wish_id"
        )
    }


def entries(c, wish_id: int) -> list[dict]:
    return [
        dict(r)
        for r in c.execute(
            "SELECT id, sponsor, amount, created_at FROM chip_ins "
            "WHERE wish_id=? ORDER BY id",
            (wish_id,),
        )
    ]
