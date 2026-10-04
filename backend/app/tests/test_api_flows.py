"""真实 HTTP 端到端回归（FastAPI TestClient + 临时 DATA_DIR）。

把线上事故逐条在接口层复现并钉死：
- 试算不落库，三处（墙 /wishes、详情 /wishes/{id}、/mine）旧累计不动；
- 确认必须凭一次性试算令牌，重放/串行作废旧令牌/无令牌全部 409 且不双记；
- 零、负金额 422 且账本无行；
- fulfill 未达标 400：状态保持 claimed，墙仍是认领中，/done 无该编号；
- 达标核销后三处一起 fulfilled、/done 出现；
- 已认领行的目标吃认领时快照：即使 target_amount 被旁路改小，
  门禁也不得按新目标放行，墙/详情/fulfill 三处同一套。
"""
import os
import tempfile
import unittest


class APIFlowCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = cls._tmp.name

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        os.environ.pop("DATA_DIR", None)

    def setUp(self):
        # 每个用例全新库文件
        db = os.path.join(self._tmp.name, "wishclaim.db")
        if os.path.exists(db):
            os.remove(db)
        from fastapi.testclient import TestClient
        from app.main import app
        self.client_cm = TestClient(app)
        self.client = self.client_cm.__enter__()
        self.addCleanup(self.client_cm.__exit__, None, None, None)

    def create(self, title="t", target=100.0):
        r = self.client.post("/api/wishes",
                             json={"title": title, "note": "n", "target_amount": target})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def claim(self, wid, claimer="zoe"):
        r = self.client.post(f"/api/wishes/{wid}/claim", json={"claimer": claimer})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def preview(self, wid, amount, sponsor="alice"):
        return self.client.post(f"/api/wishes/{wid}/chip-in",
                                json={"sponsor": sponsor, "amount": amount})

    def confirm(self, wid, amount, token=None, sponsor="alice"):
        return self.client.post(f"/api/wishes/{wid}/chip-in",
                                json={"sponsor": sponsor, "amount": amount,
                                      "confirm": True, "token": token})

    def wall_row(self, wid):
        return next(w for w in self.client.get("/api/wishes").json() if w["id"] == wid)

    # ---- 试算不落库、三处旧累计不动 ----
    def test_preview_does_not_persist_and_three_places_stay_old(self):
        wid = self.create(target=100)
        self.claim(wid)
        # 先有一笔真账 10
        tok = self.preview(wid, 10).json()["preview_token"]
        self.assertEqual(self.confirm(wid, 10, tok).status_code, 200)

        r = self.preview(wid, 30)
        self.assertEqual(r.status_code, 200, r.text)
        pv = r.json()
        self.assertFalse(pv["confirmed"])
        self.assertEqual(pv["contributed"], 10)          # 旧累计
        self.assertEqual(pv["projected_total"], 40)
        self.assertIn("preview_token", pv)

        # 详情：账本只有早先那一笔，进度仍是 10
        d = self.client.get(f"/api/wishes/{wid}").json()
        self.assertEqual(len(d["chip_ins"]), 1)
        self.assertEqual(d["progress"]["contributed"], 10)
        self.assertEqual(d["progress"]["gap"], 90)
        # 墙角标旧累计
        self.assertEqual(self.wall_row(wid)["progress"]["contributed"], 10)
        # 我的认领旁注旧累计
        mine = self.client.get("/api/mine", params={"claimer": "zoe"}).json()
        self.assertEqual(next(w for w in mine if w["id"] == wid)["progress"]["contributed"], 10)

    def test_confirm_without_token_rejected(self):
        wid = self.create()
        self.claim(wid)
        r = self.confirm(wid, 30, token=None)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"], "preview_token_required")
        self.assertEqual(self.client.get(f"/api/wishes/{wid}").json()["chip_ins"], [])

    def test_confirm_writes_exactly_one_row_and_replay_is_rejected(self):
        wid = self.create()
        self.claim(wid)
        tok = self.preview(wid, 30).json()["preview_token"]
        r1 = self.confirm(wid, 30, tok)
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertEqual(r1.json()["contributed"], 30)
        # 同一令牌叠确认 → 409，不双记
        r2 = self.confirm(wid, 30, tok)
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(r2.json()["detail"], "preview_token_replayed")
        d = self.client.get(f"/api/wishes/{wid}").json()
        self.assertEqual(len(d["chip_ins"]), 1)
        self.assertEqual(d["progress"]["contributed"], 30)

    def test_stacked_previsions_only_latest_token_confirms_once(self):
        wid = self.create()
        self.claim(wid)
        t1 = self.preview(wid, 30).json()["preview_token"]
        t2 = self.preview(wid, 50).json()["preview_token"]
        self.assertNotEqual(t1, t2)
        # 拿第一笔试算令牌确认 → 已被第二笔作废
        r_old = self.confirm(wid, 30, t1)
        self.assertEqual(r_old.status_code, 409)
        self.assertEqual(r_old.json()["detail"], "preview_token_stale")
        # 用第二笔令牌但塞了第一笔的金额 → 金额不匹配
        r_mis = self.confirm(wid, 30, t2)
        self.assertEqual(r_mis.status_code, 409)
        self.assertEqual(r_mis.json()["detail"], "preview_token_mismatch")
        # 正确确认只记一笔 50
        r_ok = self.confirm(wid, 50, t2)
        self.assertEqual(r_ok.status_code, 200, r_ok.text)
        d = self.client.get(f"/api/wishes/{wid}").json()
        self.assertEqual(len(d["chip_ins"]), 1)
        self.assertEqual(d["progress"]["contributed"], 50)

    def test_zero_and_negative_amounts_never_hit_ledger(self):
        wid = self.create()
        self.claim(wid)
        for bad in (0, -5):
            rp = self.preview(wid, bad)
            self.assertEqual(rp.status_code, 422, bad)
            rc = self.confirm(wid, bad, token="whatever")
            self.assertEqual(rc.status_code, 422, bad)
        self.assertEqual(self.client.get(f"/api/wishes/{wid}").json()["chip_ins"], [])

    # ---- fulfill 失败/成功：三处同一状态 ----
    def test_fulfill_failure_keeps_claimed_everywhere_and_nothing_in_done(self):
        wid = self.create(target=100)
        self.claim(wid)
        tok = self.preview(wid, 80).json()["preview_token"]
        self.assertEqual(self.confirm(wid, 80, tok).status_code, 200)

        r = self.client.post(f"/api/wishes/{wid}/fulfill")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"], "target_not_reached")

        # 墙卡仍是认领中
        self.assertEqual(self.wall_row(wid)["status"], "claimed")
        # 详情仍是认领中
        self.assertEqual(self.client.get(f"/api/wishes/{wid}").json()["status"], "claimed")
        # 已完成页无该编号，且愿望总数没有多出幻影行
        done_ids = [w["id"] for w in self.client.get("/api/done").json()]
        self.assertNotIn(wid, done_ids)
        wall_ids = [w["id"] for w in self.client.get("/api/wishes").json()]
        self.assertEqual(wall_ids.count(wid), 1)

    def test_successful_fulfill_appears_consistently_in_three_places(self):
        wid = self.create(target=100)
        self.claim(wid)
        for amt in (40, 60):
            tok = self.preview(wid, amt).json()["preview_token"]
            self.assertEqual(self.confirm(wid, amt, tok).status_code, 200)
        r = self.client.post(f"/api/wishes/{wid}/fulfill")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "fulfilled")
        self.assertEqual(self.wall_row(wid)["status"], "fulfilled")
        done = self.client.get("/api/done").json()
        done_row = next(w for w in done if w["id"] == wid)
        self.assertEqual(done_row["contributed_snapshot"], 100)
        # fulfilled 后不能再赞助
        again = self.confirm(wid, 1, "x", sponsor="a")
        self.assertEqual(again.status_code, 409)

    # ---- 钉住目标：认领后旁路改目标，门禁/墙/详情仍吃快照 ----
    def test_gate_wall_detail_all_use_claimed_snapshot_target(self):
        from app.db import connect
        wid = self.create(target=100)
        self.claim(wid)
        # 规则页在认领后改目标应被拒
        blocked = self.client.patch(f"/api/wishes/{wid}/target",
                                    json={"target_amount": 1000})
        self.assertEqual(blocked.status_code, 409)

        # 旁路把当前目标改小到 50（模拟脏数据/旧版本写入），快照仍是 100
        c = connect()
        c.execute("UPDATE wishes SET target_amount=50 WHERE id=?", (wid,))
        c.commit(); c.close()

        # 只凑到 60：按新目标 50 会被错误放行，按快照 100 必须拒
        tok = self.preview(wid, 60).json()["preview_token"]
        self.assertEqual(self.confirm(wid, 60, tok).status_code, 200)
        r = self.client.post(f"/api/wishes/{wid}/fulfill")
        self.assertEqual(r.status_code, 400)
        # 墙与详情展示的目标都是快照 100、缺口 40，而非 50
        wall = self.wall_row(wid)
        self.assertEqual(wall["progress"]["target_amount"], 100)
        self.assertEqual(wall["progress"]["gap"], 40)
        detail = self.client.get(f"/api/wishes/{wid}").json()
        self.assertEqual(detail["progress"]["target_amount"], 100)
        self.assertEqual(detail["progress"]["funded"], False)
        self.assertNotIn(wid, [w["id"] for w in self.client.get("/api/done").json()])

        # 凑满快照 100 后放行，三处一起完成
        tok = self.preview(wid, 40).json()["preview_token"]
        self.assertEqual(self.confirm(wid, 40, tok).status_code, 200)
        r = self.client.post(f"/api/wishes/{wid}/fulfill")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.wall_row(wid)["status"], "fulfilled")
        self.assertIn(wid, [w["id"] for w in self.client.get("/api/done").json()])


if __name__ == "__main__":
    unittest.main()
