from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.claim_lock import claim_allowed, lock_payload, release_if_expired
from app.modules import chipin
from app.modules.progress import effective_target, project, preview, decorate
from app.modules.fulfill_gate import can_fulfill

app = FastAPI(title="Wishclaim", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

def now(): return datetime.now(timezone.utc)

def ttl():
    c = connect(); row = c.execute("SELECT value FROM settings WHERE key='ttl_seconds'").fetchone(); c.close()
    return int(row["value"] if row else 86400)

def sweep(c):
    for r in c.execute("SELECT * FROM wishes WHERE status='claimed'"):
        rel = release_if_expired(r["status"], r["expires_at"], now())
        if rel:
            c.execute(
                "UPDATE wishes SET status=?, claimer=?, claimed_at=?, expires_at=?, "
                "claimed_target_amount=NULL WHERE id=?",
                (rel["status"], None, None, None, r["id"]))

def _contributed(c, r):
    """fulfilled 钉写入时累计快照，其余状态实时聚合账本。"""
    if r["status"] == "fulfilled":
        return r["contributed_snapshot"] or 0
    return chipin.total(c, r["id"])

def serialize(c, r) -> dict:
    w = dict(r)
    decorate(w, _contributed(c, r))
    return w

@app.get("/api/health")
def health(): return {"ok": True, "project": "wishclaim"}

@app.get("/api/wishes")
def list_wishes():
    c = connect(); sweep(c); c.commit()
    totals = chipin.totals_map(c)
    rows = []
    for r in c.execute("SELECT * FROM wishes ORDER BY id DESC"):
        w = dict(r)
        contributed = totals.get(w["id"], 0) if w["status"] != "fulfilled" else (w["contributed_snapshot"] or 0)
        decorate(w, contributed)
        rows.append(w)
    c.close(); return rows

@app.get("/api/wishes/{wid}")
def get_wish(wid: int):
    c = connect(); sweep(c); c.commit()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    w = serialize(c, r)
    w["chip_ins"] = chipin.entries(c, wid) if r["status"] != "fulfilled" else []
    c.close(); return w

class WishIn(BaseModel):
    title: str
    note: str = ""
    target_amount: float | None = None

@app.post("/api/wishes")
def create_wish(body: WishIn):
    target = _valid_target(body.target_amount)
    c = connect()
    cur = c.execute(
        "INSERT INTO wishes(title,note,status,data_quality,target_amount) VALUES (?,?,?,?,?)",
        (body.title, body.note, "open", "clean", target))
    c.commit(); wid = cur.lastrowid; c.close(); return {"id": wid}

def _valid_target(value):
    if value is None:
        return None
    if value <= 0:
        raise HTTPException(422, "target_amount_must_be_positive")
    return round(float(value), 2)

class TargetIn(BaseModel):
    target_amount: float | None = None

@app.patch("/api/wishes/{wid}/target")
def update_target(wid: int, body: TargetIn):
    """改 target 只影响未认领愿望；已认领的目标快照不回刷。"""
    target = _valid_target(body.target_amount)
    c = connect(); sweep(c); c.commit()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    if r["status"] in ("claimed", "fulfilled"):
        c.close(); raise HTTPException(409, "target_locked_after_claim")
    c.execute("UPDATE wishes SET target_amount=? WHERE id=?", (target, wid))
    c.commit(); c.close(); return {"ok": True, "target_amount": target}

class ClaimIn(BaseModel):
    claimer: str

@app.post("/api/wishes/{wid}/claim")
def claim(wid: int, body: ClaimIn):
    c = connect(); sweep(c); c.commit()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    allowed = claim_allowed(r["status"], r["claimer"], now(), r["expires_at"])
    if not allowed["ok"]:
        c.close(); raise HTTPException(409, allowed["reason"])
    p = lock_payload(body.claimer, now(), ttl())
    c.execute(
        "UPDATE wishes SET status=?, claimer=?, claimed_at=?, expires_at=?, "
        "claimed_target_amount=target_amount WHERE id=?",
        (p["status"], p["claimer"], p["claimed_at"], p["expires_at"], wid))
    c.commit()
    r2 = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    w = serialize(c, r2); c.close(); return w

@app.post("/api/wishes/{wid}/release")
def release(wid: int):
    c = connect()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    if r["status"] != "claimed":
        c.close(); raise HTTPException(400, "not_claimed")
    c.execute(
        "UPDATE wishes SET status='released', claimer=NULL, claimed_at=NULL, "
        "expires_at=NULL, claimed_target_amount=NULL WHERE id=?", (wid,))
    c.commit(); c.close(); return {"ok": True, "status": "released"}

class ChipInIn(BaseModel):
    sponsor: str
    amount: float
    confirm: bool = False

@app.post("/api/wishes/{wid}/chip-in")
def chip_in(wid: int, body: ChipInIn):
    """preview（confirm=false）只返回累计与缺口，绝不写库；confirm 才记账且只记一次。"""
    sponsor = chipin.normalize_sponsor(body.sponsor)
    if not sponsor:
        raise HTTPException(422, "sponsor_required")
    chk = chipin.validate_amount(body.amount)
    if not chk["ok"]:
        raise HTTPException(422, chk["reason"])
    amount = round(float(body.amount), 2)
    if amount <= 0:
        # 原始值 >0 但四舍五入到分后为 0（如 0.001），同样拒写。
        raise HTTPException(422, "amount_not_positive")
    c = connect(); sweep(c); c.commit()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    if r["status"] == "fulfilled":
        c.close(); raise HTTPException(409, "already_fulfilled")
    target = effective_target(r["status"], r["target_amount"], r["claimed_target_amount"])
    contributed = chipin.total(c, wid)
    if not body.confirm:
        pv = preview(contributed, target, amount)
        c.close()
        return {"ok": True, "confirmed": False, "sponsor": sponsor, **pv}
    chipin.add(c, wid, sponsor, amount, now().isoformat())
    c.commit()
    new_total = chipin.total(c, wid)
    c.close()
    return {"ok": True, "confirmed": True, "sponsor": sponsor,
            **project(new_total, target), "projected_total": new_total}

@app.post("/api/wishes/{wid}/fulfill")
def fulfill(wid: int):
    c = connect(); sweep(c); c.commit()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    contributed = chipin.total(c, wid)
    target = effective_target(r["status"], r["target_amount"], r["claimed_target_amount"])
    gate = can_fulfill(r["status"], target, contributed)
    if not gate["ok"]:
        # 门禁不过：只 4xx，行保持 claimed，不落任何库、不进已完成。
        c.close(); raise HTTPException(400, gate["reason"])
    c.execute(
        "UPDATE wishes SET status='fulfilled', contributed_snapshot=? WHERE id=?",
        (contributed, wid))
    c.commit()
    r2 = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    w = serialize(c, r2); c.close(); return w

@app.get("/api/mine")
def mine(claimer: str):
    c = connect(); sweep(c); c.commit()
    totals = chipin.totals_map(c)
    rows = []
    for r in c.execute("SELECT * FROM wishes WHERE claimer=?", (claimer,)):
        w = dict(r)
        decorate(w, totals.get(w["id"], 0))
        rows.append(w)
    c.close(); return rows

@app.get("/api/done")
def done():
    """已完成只认 fulfilled；claimed 行即使有赞助记录也不进此列表。"""
    c = connect()
    rows = [serialize(c, r) for r in c.execute(
        "SELECT * FROM wishes WHERE status='fulfilled' ORDER BY id DESC")]
    c.close(); return rows

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows

@app.get("/api/rules")
def rules():
    return {
        "mutex": "同一愿望同时只能被一人认领",
        "ttl": "认领超时未核销则自动释放",
        "fulfill": "核销后状态变为 fulfilled",
        "chipin": "愿望可设目标金额，任何人都可凑份子；单笔金额必须大于 0",
        "tracks": "认领人与赞助人分轨记录，同一人可以既认领又赞助",
        "target_snapshot": "目标在认领时快照，认领后修改目标不回刷；释放后重新认领才按新目标",
        "fulfill_gate": "有目标的愿望须凑满目标金额才能核销，未达标保持认领中",
    }
