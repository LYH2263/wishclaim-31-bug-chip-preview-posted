# Wishclaim · 礼物愿望认领

发布 → 认领锁定（互斥+TTL）→ 凑份子赞助 → 达标核销/释放。

| 服务 | 端口 |
| --- | --- |
| 前端 | 5200 |
| API | 10200 |

```bash
docker compose up --build
pytest backend/app/tests
```

## 凑份子（chip-in）

- 愿望可设 `target_amount`；未认领时可随时 PATCH 修改。
- `POST /api/wishes/{id}/chip-in`：`confirm=false` 仅预览（返回累计、缺口、
  假想累计，**不写库**）；`confirm=true` 才记账累加。单笔金额 ≤ 0 拒绝。
- 认领人与赞助人**分轨**：同一人允许既认领又赞助。
- 目标在**认领时快照**（`claimed_target_amount`），认领后改 target 不回刷；
  释放/TTL 超时后快照清空，重新认领按新目标。
- 核销门禁：有目标的愿望必须累计 ≥ 目标才允许 fulfill，否则保持 `claimed`。
- 核销时把累计写入 `contributed_snapshot`，已完成页钉的是该快照，
  不再随赞助账本变动。

模块：`app/modules/chipin`（赞助账本）· `app/modules/progress`（进度投影/预览）
· `app/modules/fulfill_gate`（核销门禁），各带测例。

0-1：`wish_comment` / `secret_santa` / `price_cap`。
