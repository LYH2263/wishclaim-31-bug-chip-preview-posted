from app.db import connect
from app.modules import chipin


def _migrate(c):
    cols = [r["name"] for r in c.execute("PRAGMA table_info(wishes)")]
    for col in ("target_amount", "claimed_target_amount", "contributed_snapshot"):
        if col not in cols:
            c.execute(f"ALTER TABLE wishes ADD COLUMN {col} REAL")
    chipin.create_table(c)


def init_db():
    c = connect()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS wishes(
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, note TEXT, status TEXT,
      claimer TEXT, claimed_at TEXT, expires_at TEXT, data_quality TEXT,
      target_amount REAL, claimed_target_amount REAL, contributed_snapshot REAL
    );
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    """)
    _migrate(c)
    if c.execute("SELECT COUNT(*) c FROM wishes").fetchone()["c"] == 0:
        c.executemany(
            "INSERT INTO wishes(title,note,status,claimer,claimed_at,expires_at,"
            "data_quality,target_amount,claimed_target_amount,contributed_snapshot) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("机械键盘", "红轴", "open", None, None, None, "clean", 200, None, None),
                ("围巾", "羊毛", "open", None, None, None, "clean", 80, None, None),
                ("脏愿望-空标题", "", "open", None, None, None, "dirty", None, None, None),
                ("过期锁样例", "应被TTL释放", "claimed", "ghost", "2020-01-01T00:00:00+00:00",
                 "2020-01-01T01:00:00+00:00", "dirty", 50, 50, None),
                ("拼图", "凑份子达标后核销样例", "fulfilled", "zoe",
                 "2026-09-30T09:00:00+00:00", "2026-09-30T10:00:00+00:00",
                 "clean", 1000, 1000, 1000),
            ],
        )
        c.executemany(
            "INSERT INTO chip_ins(wish_id,sponsor,amount,created_at) VALUES (?,?,?,?)",
            [
                (5, "alice", 600, "2026-09-30T09:20:00+00:00"),
                (5, "bob", 400, "2026-09-30T09:40:00+00:00"),
            ],
        )
        c.execute("INSERT INTO settings(key,value) VALUES ('ttl_seconds','86400')")
        c.execute("INSERT INTO settings(key,value) VALUES ('wall_title','暖粉愿望墙')")
        c.commit()
    c.close()
