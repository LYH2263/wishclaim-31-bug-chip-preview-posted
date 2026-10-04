"""凑份子/核销一致性回归测试。

拍板口径：
- 试算（confirm=false）绝不写账本；确认才入账且同一笔只记一次。
- 已认领行的达标判定吃认领时钉住的目标快照，墙缺口/详情进度/fulfill 门禁同一套。
- fulfill 失败 = 三处都保持认领中，已完成列表无该编号；成功 = 三处一起 fulfilled。
- 零/负数（含四舍五入到分后为 0）一律 422，不进账本。
"""
import pytest
from fastapi.testclient import TestClient

from app import seed
from app.db import connect
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    with TestClient(app) as c:
        yield c


def mkwish(client, target=100):
    r = client.post("/api/wishes", json={"title": "t", "note": "", "target_amount": target})
    assert r.status_code == 200
    return r.json()["id"]


def claim(client, wid, who="neo"):
    r = client.post(f"/api/wishes/{wid}/claim", json={"claimer": who})
    assert r.status_code == 200


def chip(client, wid, amount, confirm, sponsor="alice"):
    return client.post(
        f"/api/wishes/{wid}/chip-in",
        json={"sponsor": sponsor, "amount": amount, "confirm": confirm},
    )


def wall_row(client, wid):
    return next(w for w in client.get("/api/wishes").json() if w["id"] == wid)


def mine_row(client, wid, who="neo"):
    return next(w for w in client.get("/api/mine", params={"claimer": who}).json() if w["id"] == wid)


def done_ids(client):
    return [w["id"] for w in client.get("/api/done").json()]


def test_preview_never_writes_confirm_writes_once(client):
    wid = mkwish(client, 100)
    claim(client, wid)

    pv = chip(client, wid, 30, confirm=False).json()
    assert pv["confirmed"] is False
    assert pv["contributed"] == 0 and pv["projected_total"] == 30
    assert pv["gap"] == 100 and pv["projected_gap"] == 70

    # 试算后三处都还是旧累计：详情 / 墙角标 / 我的认领旁注
    d = client.get(f"/api/wishes/{wid}").json()
    assert d["chip_ins"] == [] and d["progress"]["contributed"] == 0
    assert wall_row(client, wid)["progress"]["contributed"] == 0
    assert mine_row(client, wid)["progress"]["contributed"] == 0

    # 再叠一笔试算，账本仍然不动
    chip(client, wid, 30, confirm=False)
    assert client.get(f"/api/wishes/{wid}").json()["chip_ins"] == []

    # 确认一次：只记一行、只加一笔，三处一起跳到 30
    cf = chip(client, wid, 30, confirm=True).json()
    assert cf["confirmed"] is True and cf["contributed"] == 30
    d = client.get(f"/api/wishes/{wid}").json()
    assert len(d["chip_ins"]) == 1 and d["chip_ins"][0]["amount"] == 30
    assert d["progress"]["contributed"] == 30
    assert wall_row(client, wid)["progress"]["contributed"] == 30
    assert mine_row(client, wid)["progress"]["contributed"] == 30


def test_zero_negative_and_dust_amounts_rejected(client):
    wid = mkwish(client, 100)
    for bad in (0, -5, 0.001):  # 0.001 四舍五入到分后为 0，同样拒
        assert chip(client, wid, bad, confirm=False).status_code == 422
        assert chip(client, wid, bad, confirm=True).status_code == 422
    assert client.get(f"/api/wishes/{wid}").json()["chip_ins"] == []


def test_fulfill_shortfall_keeps_claimed_and_done_clean(client):
    wid = mkwish(client, 100)
    claim(client, wid)
    chip(client, wid, 40, confirm=True)

    r = client.post(f"/api/wishes/{wid}/fulfill")
    assert r.status_code == 400 and r.json()["detail"] == "target_not_reached"

    # 失败口径：详情/墙/我的认领都还是 claimed，已完成无该编号
    assert client.get(f"/api/wishes/{wid}").json()["status"] == "claimed"
    assert wall_row(client, wid)["status"] == "claimed"
    assert mine_row(client, wid)["status"] == "claimed"
    assert wid not in done_ids(client)

    # 凑满后核销：三处一起 fulfilled，已完成出现且只出现一次
    chip(client, wid, 60, confirm=True)
    r = client.post(f"/api/wishes/{wid}/fulfill")
    assert r.status_code == 200 and r.json()["status"] == "fulfilled"
    assert wall_row(client, wid)["status"] == "fulfilled"
    assert done_ids(client).count(wid) == 1
    # 核销后钉写入时累计快照，且不再接受新赞助
    done_row = next(w for w in client.get("/api/done").json() if w["id"] == wid)
    assert done_row["contributed_snapshot"] == 100
    assert chip(client, wid, 10, confirm=True).status_code == 409


def test_fulfill_requires_claim(client):
    wid = mkwish(client, None)
    r = client.post(f"/api/wishes/{wid}/fulfill")
    assert r.status_code == 400 and r.json()["detail"] == "need_claim"
    assert wid not in done_ids(client)


def test_pinned_target_governs_wall_detail_and_fulfill(client):
    wid = mkwish(client, 100)
    claim(client, wid)  # 钉住 100

    # 认领后走接口改目标被拒
    assert client.patch(f"/api/wishes/{wid}/target", json={"target_amount": 500}).status_code == 409

    # 模拟“事后改规则页”：直接改库里的可编辑目标
    c = connect()
    c.execute("UPDATE wishes SET target_amount=500 WHERE id=?", (wid,))
    c.commit(); c.close()

    chip(client, wid, 100, confirm=True)

    # 墙缺口、详情进度都按钉住的 100 画，不按改后的 500
    assert wall_row(client, wid)["progress"]["target_amount"] == 100
    assert wall_row(client, wid)["progress"]["gap"] == 0
    d = client.get(f"/api/wishes/{wid}").json()
    assert d["progress"]["target_amount"] == 100 and d["progress"]["funded"] is True

    # fulfill 同一套：按钉住的 100 放行
    assert client.post(f"/api/wishes/{wid}/fulfill").status_code == 200


def test_pinned_target_not_relaxed_when_target_lowered(client):
    wid = mkwish(client, 100)
    claim(client, wid)  # 钉住 100
    chip(client, wid, 75, confirm=True)

    c = connect()
    c.execute("UPDATE wishes SET target_amount=50 WHERE id=?", (wid,))
    c.commit(); c.close()

    # 改低目标也不放行：仍按钉住的 100 判定，保持认领中
    r = client.post(f"/api/wishes/{wid}/fulfill")
    assert r.status_code == 400 and r.json()["detail"] == "target_not_reached"
    assert wall_row(client, wid)["status"] == "claimed"
    assert wid not in done_ids(client)


def test_open_wish_target_follows_editable_target(client):
    wid = mkwish(client, 100)
    assert client.patch(f"/api/wishes/{wid}/target", json={"target_amount": 250}).status_code == 200
    assert wall_row(client, wid)["progress"]["target_amount"] == 250
    # 重新认领后钉的是新目标
    claim(client, wid)
    d = client.get(f"/api/wishes/{wid}").json()
    assert d["progress"]["target_amount"] == 250
