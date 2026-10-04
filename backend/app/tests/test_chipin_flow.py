"""凑份子/快照目标/fulfill 门禁的回归测试（仅依赖标准库，pytest 亦可收集）。

覆盖线上事故：
- 试算不落库；确认只记一笔；令牌重放/串行试算/金额不一致/过期一律拒；
- 零或负数金额拒写；
- 已认领行达标判定吃认领时钉住的 claimed_target_amount，改大规则页目标不放行；
- fulfill 门禁不过时不产生任何 fulfilled 行，/done 只有真 fulfilled；
- 墙列表 claimed 行的进度取真实账本累计，不再恒为 0。
"""
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from sqlite3 import connect

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.modules import chipin
from app.modules.progress import effective_target, preview, project, decorate
from app.modules.fulfill_gate import can_fulfill
from app.engines.claim_lock import parse_ts

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


class DBCase(unittest.TestCase):
    def setUp(self):
        self.c = connect(":memory:")
        self.c.row_factory = __import__("sqlite3").Row
        chipin.create_table(self.c)
        self.c.execute(
            "CREATE TABLE wishes(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, "
            "note TEXT, status TEXT, claimer TEXT, claimed_at TEXT, expires_at TEXT, "
            "data_quality TEXT, target_amount REAL, claimed_target_amount REAL, "
            "contributed_snapshot REAL)"
        )

    def tearDown(self):
        self.c.close()

    def insert_wish(self, status="claimed", target=100.0, claimed_target=100.0,
                    snapshot=None):
        cur = self.c.execute(
            "INSERT INTO wishes(title,note,status,claimer,claimed_at,expires_at,"
            "data_quality,target_amount,claimed_target_amount,contributed_snapshot) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("t", "n", status, "zoe" if status in ("claimed", "fulfilled") else None,
             None, None, "clean", target,
             claimed_target if status in ("claimed", "fulfilled") else None, snapshot),
        )
        return cur.lastrowid

    # ---- 试算/确认/幂等 ----
    def test_preview_does_not_write_ledger(self):
        wid = self.insert_wish()
        pv = preview(chipin.total(self.c, wid), 100.0, 30.0)
        token = chipin.issue_token(self.c, wid, "alice", 30.0, NOW)
        self.assertTrue(token)
        self.assertEqual(chipin.total(self.c, wid), 0)
        self.assertEqual(chipin.entries(self.c, wid), [])
        self.assertEqual(pv["contributed"], 0)
        self.assertEqual(pv["projected_total"], 30.0)
        self.assertEqual(pv["projected_gap"], 70.0)

    def test_confirm_consumes_token_once_and_writes_one_row(self):
        wid = self.insert_wish()
        token = chipin.issue_token(self.c, wid, "alice", 30.0, NOW)
        self.c.commit()  # 试算请求结束即提交；确认是独立请求、新事务
        # 按端点顺序：BEGIN IMMEDIATE -> consume -> add
        self.c.execute("BEGIN IMMEDIATE")
        ok = chipin.consume_token(self.c, token, wid, "alice", 30.0, NOW)
        self.assertTrue(ok["ok"], ok)
        chipin.add(self.c, wid, "alice", 30.0, NOW.isoformat())
        self.c.commit()
        self.assertEqual(chipin.total(self.c, wid), 30.0)
        self.assertEqual(len(chipin.entries(self.c, wid)), 1)
        # 同一令牌再确认（叠确认）必须拒，不得双记
        again = chipin.consume_token(self.c, token, wid, "alice", 30.0, NOW)
        self.assertFalse(again["ok"])
        self.assertEqual(again["reason"], "preview_token_replayed")
        self.assertEqual(chipin.total(self.c, wid), 30.0)
        self.assertEqual(len(chipin.entries(self.c, wid)), 1)

    def test_two_previews_supersede_first_token(self):
        wid = self.insert_wish()
        t1 = chipin.issue_token(self.c, wid, "alice", 30.0, NOW)
        t2 = chipin.issue_token(self.c, wid, "alice", 30.0, NOW)
        self.assertNotEqual(t1, t2)
        # 拿第一笔试算的令牌去确认（两笔试算叠确认）→ 拒
        stale = chipin.consume_token(self.c, t1, wid, "alice", 30.0, NOW)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["reason"], "preview_token_stale")
        self.assertEqual(chipin.total(self.c, wid), 0)
        # 最新令牌可确认且只记一笔
        ok = chipin.consume_token(self.c, t2, wid, "alice", 30.0, NOW)
        self.assertTrue(ok["ok"])
        chipin.add(self.c, wid, "alice", 30.0, NOW.isoformat())
        self.assertEqual(chipin.total(self.c, wid), 30.0)

    def test_token_amount_mismatch_rejected(self):
        wid = self.insert_wish()
        token = chipin.issue_token(self.c, wid, "alice", 30.0, NOW)
        bad = chipin.consume_token(self.c, token, wid, "alice", 50.0, NOW)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason"], "preview_token_mismatch")
        # 未消费，原金额仍可用
        ok = chipin.consume_token(self.c, token, wid, "alice", 30.0, NOW)
        self.assertTrue(ok["ok"])

    def test_token_expired_rejected(self):
        wid = self.insert_wish()
        token = chipin.issue_token(self.c, wid, "alice", 30.0, NOW, ttl_seconds=600)
        expired = chipin.consume_token(self.c, token, wid, "alice", 30.0,
                                       NOW + timedelta(seconds=601))
        self.assertFalse(expired["ok"])
        self.assertEqual(expired["reason"], "preview_token_expired")
        self.assertEqual(chipin.total(self.c, wid), 0)

    def test_token_wrong_wish_or_sponsor_rejected(self):
        wid = self.insert_wish()
        token = chipin.issue_token(self.c, wid, "alice", 30.0, NOW)
        self.assertFalse(chipin.consume_token(self.c, token, wid + 1, "alice", 30.0, NOW)["ok"])
        self.assertFalse(chipin.consume_token(self.c, token, wid, "bob", 30.0, NOW)["ok"])
        self.assertEqual(chipin.consume_token(self.c, None, wid, "alice", 30.0, NOW)["reason"],
                         "preview_token_required")
        self.assertEqual(chipin.consume_token(self.c, "nope", wid, "alice", 30.0, NOW)["reason"],
                         "preview_token_unknown")

    def test_zero_and_negative_amounts_rejected(self):
        for bad in (0, -1, -0.01, float("nan"), float("inf"), float("-inf"), "10", True):
            r = chipin.validate_amount(bad)
            self.assertFalse(r["ok"], f"should reject {bad!r}: {r}")
        self.assertTrue(chipin.validate_amount(0.01)["ok"])

    # ---- 钉住目标：三处同一套 ----
    def test_effective_target_uses_snapshot_once_claimed(self):
        self.assertEqual(effective_target("open", 200.0, None), 200.0)
        self.assertEqual(effective_target("released", 500.0, None), 500.0)
        # 认领后规则页把目标从 100 改到 1000：仍吃钉住的 100
        self.assertEqual(effective_target("claimed", 1000.0, 100.0), 100.0)
        self.assertEqual(effective_target("fulfilled", 1000.0, 100.0), 100.0)

    def test_raising_target_after_claim_does_not_unblock_or_block_fulfill(self):
        wid = self.insert_wish(status="claimed", target=100.0, claimed_target=100.0)
        chipin.add(self.c, wid, "alice", 100.0, NOW.isoformat())
        # 规则页目标改大到 1000（模拟 target_amount 被更新，快照不动）
        self.c.execute("UPDATE wishes SET target_amount=1000 WHERE id=?", (wid,))
        row = self.c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
        contributed = chipin.total(self.c, wid)
        target = effective_target(row["status"], row["target_amount"], row["claimed_target_amount"])
        # 门禁吃钉住的 100 → 已达标，放行；不会按新目标 1000 判未达标
        gate = can_fulfill(row["status"], target, contributed)
        self.assertTrue(gate["ok"], gate)

    def test_fulfill_gate_blocks_underfunded_without_creating_rows(self):
        wid = self.insert_wish(status="claimed", target=100.0, claimed_target=100.0)
        chipin.add(self.c, wid, "alice", 80.0, NOW.isoformat())
        row = self.c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
        contributed = chipin.total(self.c, wid)
        target = effective_target(row["status"], row["target_amount"], row["claimed_target_amount"])
        gate = can_fulfill(row["status"], target, contributed)
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["reason"], "target_not_reached")
        # 门禁不过：端点不得 INSERT/UPDATE —— 断言仍是 claimed 且无新愿望行
        self.assertEqual(self.c.execute("SELECT COUNT(*) n FROM wishes").fetchone()["n"], 1)
        self.assertEqual(self.c.execute("SELECT status FROM wishes WHERE id=?", (wid,))
                         .fetchone()["status"], "claimed")

    # ---- 列表口径 ----
    def test_done_lists_only_fulfilled(self):
        claimed = self.insert_wish(status="claimed")
        chipin.add(self.c, claimed, "alice", 50.0, NOW.isoformat())
        done_id = self.insert_wish(status="fulfilled", target=100.0,
                                   claimed_target=100.0, snapshot=100.0)
        rows = self.c.execute("SELECT id FROM wishes WHERE status='fulfilled'").fetchall()
        ids = [r["id"] for r in rows]
        self.assertIn(done_id, ids)
        self.assertNotIn(claimed, ids)  # 有账本的 claimed 不得混进已完成页

    def test_wall_claimed_progress_uses_real_ledger_total(self):
        # 复刻 list_wishes 对 claimed 行的 decorate 口径
        wid = self.insert_wish(status="claimed", target=100.0, claimed_target=100.0)
        chipin.add(self.c, wid, "alice", 40.0, NOW.isoformat())
        totals = chipin.totals_map(self.c)
        row = dict(self.c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone())
        decorate(row, totals[wid])  # 旧代码这里传 0，墙角标停在试算前
        self.assertEqual(row["progress"]["contributed"], 40.0)
        self.assertEqual(row["progress"]["target_amount"], 100.0)
        self.assertEqual(row["progress"]["gap"], 60.0)
        self.assertEqual(row["progress"]["funded"], False)

    def test_preview_to_confirm_three_places_move_together(self):
        """试算前后累计投影不变；确认后新投影恰好加这一笔。"""
        wid = self.insert_wish(status="claimed", target=100.0, claimed_target=100.0)
        chipin.add(self.c, wid, "bob", 10.0, NOW.isoformat())
        before = chipin.total(self.c, wid)
        chipin.issue_token(self.c, wid, "alice", 30.0, NOW)
        # 试算后三处旧累计不变
        self.assertEqual(chipin.total(self.c, wid), before)
        token = chipin.issue_token(self.c, wid, "alice", 30.0, NOW)
        ok = chipin.consume_token(self.c, token, wid, "alice", 30.0, NOW)
        self.assertTrue(ok["ok"])
        chipin.add(self.c, wid, "alice", 30.0, NOW.isoformat())
        total = chipin.total(self.c, wid)
        p = project(total, effective_target("claimed", 100.0, 100.0))
        self.assertEqual(total, 40.0)
        self.assertEqual(p["gap"], 60.0)


if __name__ == "__main__":
    unittest.main()
