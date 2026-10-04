"""赞助账本（chip-in ledger）。

与认领锁（claimer 轨）完全分离：这里只认 sponsor 名字与金额，
同一人既认领又赞助不受限制，由两条轨道各自记录。

试算（preview）不落库，只签发一次性令牌 token；确认（confirm）必须出示
与本次 (wish, sponsor, amount) 完全一致且未被消费的 token，从根上杜绝
「试算一返回账本多一行」与「两笔试算叠确认双记同一金额」。
"""
import math
import secrets
from datetime import datetime, timedelta

from app.engines.claim_lock import parse_ts

PREVIEW_TTL_SECONDS = 600

DDL = """
CREATE TABLE IF NOT EXISTS chip_ins(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  wish_id INTEGER NOT NULL,
  sponsor TEXT NOT NULL,
  amount REAL NOT NULL,
  created_at TEXT NOT NULL
)
"""

TOKEN_DDL = """
CREATE TABLE IF NOT EXISTS chip_in_tokens(
  nonce TEXT PRIMARY KEY,
  wish_id INTEGER NOT NULL,
  sponsor TEXT NOT NULL,
  amount REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  expires_at TEXT NOT NULL
)
"""


def create_table(c):
    c.execute(DDL)
    c.execute(TOKEN_DDL)


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


def issue_token(c, wish_id: int, sponsor: str, amount: float, at: datetime,
                ttl_seconds: int = PREVIEW_TTL_SECONDS) -> str:
    """试算签发一次性令牌；同一赞助人新试算自动作废旧令牌（后一笔为准）。"""
    amount = round(float(amount), 2)
    c.execute(
        "UPDATE chip_in_tokens SET status='superseded' "
        "WHERE wish_id=? AND sponsor=? AND status='open'",
        (wish_id, sponsor),
    )
    nonce = secrets.token_hex(16)
    c.execute(
        "INSERT INTO chip_in_tokens(nonce,wish_id,sponsor,amount,status,expires_at) "
        "VALUES (?,?,?,?,'open',?)",
        (nonce, wish_id, sponsor, amount,
         (at + timedelta(seconds=ttl_seconds)).isoformat()),
    )
    return nonce


def consume_token(c, token, wish_id: int, sponsor: str, amount: float,
                  at: datetime) -> dict:
    """原子消费令牌：必须存在、属于本愿望/赞助人、金额一致、open、未过期。"""
    if not isinstance(token, str) or not token:
        return {"ok": False, "reason": "preview_token_required"}
    row = c.execute(
        "SELECT * FROM chip_in_tokens WHERE nonce=?", (token,)
    ).fetchone()
    if row is None:
        return {"ok": False, "reason": "preview_token_unknown"}
    if row["status"] == "consumed":
        return {"ok": False, "reason": "preview_token_replayed"}
    if row["status"] != "open":
        return {"ok": False, "reason": "preview_token_stale"}
    if parse_ts(row["expires_at"]) <= at:
        c.execute("UPDATE chip_in_tokens SET status='expired' WHERE nonce=?", (token,))
        return {"ok": False, "reason": "preview_token_expired"}
    amount = round(float(amount), 2)
    if row["wish_id"] != wish_id or row["sponsor"] != sponsor or abs(float(row["amount"]) - amount) >= 0.005:
        return {"ok": False, "reason": "preview_token_mismatch"}
    cur = c.execute(
        "UPDATE chip_in_tokens SET status='consumed' "
        "WHERE nonce=? AND status='open'",
        (token,),
    )
    if cur.rowcount != 1:
        return {"ok": False, "reason": "preview_token_replayed"}
    return {"ok": True, "reason": ""}
