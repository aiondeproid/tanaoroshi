import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("TANAOROSHI_DB", Path(__file__).resolve().parent.parent / "data" / "tanaoroshi.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    grp TEXT NOT NULL,              -- 元のファイル単位のまとまり（タレ液体、粉体資材 など）
    name TEXT NOT NULL,
    locations TEXT NOT NULL DEFAULT 'split',  -- split=資材室と倉庫/パレット / single=1か所
    sort INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    storage TEXT NOT NULL DEFAULT '常温',      -- 常温/冷蔵/冷凍
    case_weight REAL,                         -- kg。NULL=未設定
    expiry_mode TEXT NOT NULL DEFAULT 'date', -- date=期限を入力 / mfg=製造日+月数 / none=期限指定なし
    mfg_months INTEGER,
    note TEXT NOT NULL DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1         -- 0=終売
);
CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    sort INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    ym TEXT NOT NULL,                 -- 棚卸月 YYYY-MM
    item_id INTEGER NOT NULL REFERENCES items(id),
    room_cases REAL NOT NULL DEFAULT 0,
    room_kg REAL NOT NULL DEFAULT 0,  -- 端数
    wh_cases REAL NOT NULL DEFAULT 0,
    wh_kg REAL NOT NULL DEFAULT 0,
    room_kg_parts TEXT,               -- 端数の内訳（JSON配列、合計が room_kg）
    wh_kg_parts TEXT,                 -- 端数の内訳（JSON配列、合計が wh_kg）
    case_weight REAL,                 -- 入力時点のケース重量
    total_kg REAL NOT NULL DEFAULT 0,
    expiry_kind TEXT NOT NULL DEFAULT '賞',  -- 賞/使/凍
    expiry_date TEXT,
    mfg_date TEXT,
    status TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT '',
    action_date TEXT,
    action_note TEXT NOT NULL DEFAULT '',
    counted_by TEXT NOT NULL,
    counted_at TEXT NOT NULL,         -- 保存した日時（自動）
    counted_on TEXT,                  -- 棚卸日。初期値は保存した日で、あとから直せる
    UNIQUE (ym, item_id)
);
CREATE TABLE IF NOT EXISTS expected (
    id INTEGER PRIMARY KEY,
    ym TEXT NOT NULL,                 -- 棚卸月 YYYY-MM
    item_id INTEGER NOT NULL REFERENCES items(id),
    kg REAL NOT NULL,                 -- 予想在庫（総重量と比べる）
    set_by TEXT NOT NULL,
    set_at TEXT NOT NULL,
    UNIQUE (ym, item_id)
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY,
    ym TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    role TEXT NOT NULL,
    staff_name TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL,
    UNIQUE (ym, category_id, role)
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id INTEGER,
    action TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    before_json TEXT,
    after_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_at ON history(at);
CREATE INDEX IF NOT EXISTS idx_entries_ym ON entries(ym);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        # 棚卸日の列がない古いDBには足して、保存した日で埋める
        cols = {r["name"] for r in con.execute("PRAGMA table_info(entries)")}
        if "counted_on" not in cols:
            con.execute("ALTER TABLE entries ADD COLUMN counted_on TEXT")
            con.execute("UPDATE entries SET counted_on = substr(counted_at, 1, 10)")
        # 端数の内訳の列がない古いDBには足す（NULL=内訳なし、合計だけ）
        for c in ("room_kg_parts", "wh_kg_parts"):
            if c not in cols:
                con.execute(f"ALTER TABLE entries ADD COLUMN {c} TEXT")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def row_dict(row) -> dict | None:
    return dict(row) if row is not None else None


def record(con, actor: str, entity: str, entity_id, action: str, summary: str, before=None, after=None) -> None:
    """変更履歴を1件残す。呼び出し側のトランザクションに乗る。"""
    con.execute(
        "INSERT INTO history (at, actor, entity, entity_id, action, summary, before_json, after_json)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            now(), actor, entity, entity_id, action, summary,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
        ),
    )
