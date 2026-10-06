import json
import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, unquote

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import db, expected_xlsx, export, items_xlsx, logic

ADMIN_PIN = os.environ.get("TANAOROSHI_ADMIN_PIN", "1234")
STATIC = Path(__file__).resolve().parent / "static"

# 確認フロー（紙のヘッダー欄の順）
ROLES = ["担当者", "前工程L", "生産管理", "品管"]
ROLE_HINT = {
    "担当者": "賞味期限切れ(×)の識別",
    "前工程L": "期限確認・×に赤○",
    "生産管理": "現物の識別確認",
    "品管": "期限の確認",
}
ACTIONS = ["製造計画に入れる", "移動", "廃棄", "その他"]
STORAGES = ["常温", "冷蔵", "冷凍"]

app = FastAPI(title="原料棚卸")
db.init()


# ---------- 共通 ----------

def actor(x_user: str | None = Header(default=None)) -> str:
    name = unquote(x_user or "").strip()
    if not name:
        raise HTTPException(400, "名前を選んでください")
    return name


def admin(x_admin_pin: str | None = Header(default=None), user: str = Depends(actor)) -> str:
    if x_admin_pin != ADMIN_PIN:
        raise HTTPException(403, "管理者PINが違います")
    return user


def check_ym(ym: str) -> str:
    try:
        y, m = map(int, ym.split("-"))
        date(y, m, 1)
    except ValueError:
        raise HTTPException(400, "棚卸月の形式が不正です")
    return f"{y:04d}-{m:02d}"


def parse_date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def get_item(con, item_id: int) -> dict:
    item = db.row_dict(con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())
    if not item:
        raise HTTPException(404, "品目がありません")
    return item


def category_name(con, category_id: int) -> str:
    row = con.execute("SELECT name FROM categories WHERE id=?", (category_id,)).fetchone()
    if not row:
        raise HTTPException(404, "分類がありません")
    return row["name"]


def month_items(con, ym: str, category_id: int | None = None) -> list[dict]:
    """その月に数える品目（有効な品目＋その月に入力済みの終売品）と入力内容。"""
    sql = """
        SELECT i.*, c.name AS category_name, c.locations,
               e.id AS entry_id, e.room_cases, e.room_kg, e.wh_cases, e.wh_kg, e.wh_pallets, e.room_kg_parts, e.wh_kg_parts, e.total_kg,
               e.case_weight AS entry_case_weight, e.pallet_cases AS entry_pallet_cases, e.expiry_kind, e.expiry_date, e.mfg_date,
               e.status, e.action, e.action_date, e.action_note, e.counted_by, e.counted_at, e.counted_on,
               x.kg AS expected_kg
        FROM items i
        JOIN categories c ON c.id = i.category_id
        LEFT JOIN entries e ON e.item_id = i.id AND e.ym = ?
        LEFT JOIN expected x ON x.item_id = i.id AND x.ym = ?
        WHERE (i.active = 1 OR e.id IS NOT NULL)
    """
    args: list = [ym, ym]
    if category_id is not None:
        sql += " AND i.category_id = ?"
        args.append(category_id)
    sql += " ORDER BY c.sort, i.sort, i.code"
    return [dict(r) for r in con.execute(sql, args)]


# ---------- 基本情報 ----------

@app.get("/api/meta")
def meta():
    with db.connect() as con:
        cats = [dict(r) for r in con.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM items i WHERE i.category_id=c.id AND i.active=1) AS item_count"
            " FROM categories c WHERE c.active=1 ORDER BY c.sort")]
        staff = [r["name"] for r in con.execute("SELECT name FROM staff WHERE active=1 ORDER BY sort, name")]
    return {
        "categories": cats, "staff": staff, "roles": ROLES, "role_hint": ROLE_HINT,
        "actions": ACTIONS, "storages": STORAGES, "warn_months": logic.WARN_MONTHS,
        "today": date.today().isoformat(),
    }


@app.get("/api/months/{ym}/summary")
def summary(ym: str):
    ym = check_ym(ym)
    with db.connect() as con:
        rows = month_items(con, ym)
        appr = [dict(r) for r in con.execute("SELECT * FROM approvals WHERE ym=?", (ym,))]
    out: dict[int, dict] = {}

    def empty():
        # counted_from / counted_to = その分類を棚卸した最初と最後の日
        return {"total": 0, "entered": 0, "warn": 0, "expired": 0, "approvals": {},
                "counted_from": None, "counted_to": None}

    for r in rows:
        s = out.setdefault(r["category_id"], empty())
        s["total"] += 1
        if r["entry_id"]:
            s["entered"] += 1
            day = r["counted_on"]
            s["counted_from"] = min(s["counted_from"] or day, day)
            s["counted_to"] = max(s["counted_to"] or day, day)
            if r["status"] in ("warn", "expired"):
                s[r["status"]] += 1
    for a in appr:
        out.setdefault(a["category_id"], empty())
        out[a["category_id"]]["approvals"][a["role"]] = a["staff_name"]
    return {
        "ym": ym, "month_end": logic.month_end(ym).isoformat(), "ok_limit": logic.ok_limit(ym).isoformat(),
        "categories": out,
    }


@app.get("/api/months/{ym}/categories/{category_id}")
def category_detail(ym: str, category_id: int):
    ym = check_ym(ym)
    with db.connect() as con:
        cat = db.row_dict(con.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone())
        if not cat:
            raise HTTPException(404, "分類がありません")
        items = month_items(con, ym, category_id)
        appr = {r["role"]: dict(r) for r in con.execute(
            "SELECT * FROM approvals WHERE ym=? AND category_id=?", (ym, category_id))}
    return {"category": cat, "items": items, "approvals": appr,
            "month_end": logic.month_end(ym).isoformat(), "ok_limit": logic.ok_limit(ym).isoformat()}


# ---------- 棚卸入力 ----------

class EntryIn(BaseModel):
    room_cases: float = Field(0, ge=0)
    room_kg: float = Field(0, ge=0)
    wh_cases: float = Field(0, ge=0)
    wh_kg: float = Field(0, ge=0)
    wh_pallets: float = Field(0, ge=0)
    # 端数の内訳（最大3つ）。あれば合計を room_kg / wh_kg にする
    room_kg_parts: list[Annotated[float, Field(ge=0)]] | None = Field(None, max_length=3)
    wh_kg_parts: list[Annotated[float, Field(ge=0)]] | None = Field(None, max_length=3)
    expiry_kind: Literal["賞", "使", "凍"] = "賞"
    expiry_date: str | None = None
    mfg_date: str | None = None
    action: str = ""
    action_date: str | None = None
    action_note: str = ""
    counted_on: str | None = None  # 棚卸日。None=新規は今日、入力済みは変えない


def check_counted_on(s: str | None, before: dict | None) -> str:
    if not s:
        return before["counted_on"] if before and before["counted_on"] else date.today().isoformat()
    try:
        d = date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, "棚卸日の形式が不正です")
    if d > date.today():
        raise HTTPException(400, "棚卸日に未来の日付は入れられません")
    return d.isoformat()


def build_entry(item: dict, ym: str, body: EntryIn) -> dict:
    mfg = parse_date(body.mfg_date)
    if item["expiry_mode"] == "none":
        expiry = None
    elif item["expiry_mode"] == "mfg":
        expiry = logic.expiry_from_mfg(mfg, item["mfg_months"] or 0) if mfg else None
    else:
        expiry = parse_date(body.expiry_date)
    if item["expiry_mode"] == "none":
        status = "ok"
    else:
        status = logic.judge(expiry, ym, date.today())
    cw = item["case_weight"]
    pc = item["pallet_cases"]
    if body.wh_pallets and not pc:
        raise HTTPException(400, "1パレットのケース数が未設定です。管理画面で設定してください")
    room_kg = round(sum(body.room_kg_parts), 3) if body.room_kg_parts is not None else body.room_kg
    wh_kg = round(sum(body.wh_kg_parts), 3) if body.wh_kg_parts is not None else body.wh_kg
    cases = body.room_cases + body.wh_cases + body.wh_pallets * (pc or 0)
    total = logic.total_kg(cw, cases, room_kg + wh_kg)
    keep_action = status in ("warn", "expired")
    return {
        "room_cases": body.room_cases, "room_kg": room_kg,
        "wh_cases": body.wh_cases, "wh_kg": wh_kg, "wh_pallets": body.wh_pallets,
        "room_kg_parts": json.dumps(body.room_kg_parts) if body.room_kg_parts is not None else None,
        "wh_kg_parts": json.dumps(body.wh_kg_parts) if body.wh_kg_parts is not None else None,
        "case_weight": cw, "pallet_cases": pc, "total_kg": total, "expiry_kind": body.expiry_kind,
        "expiry_date": expiry.isoformat() if expiry else None,
        "mfg_date": mfg.isoformat() if mfg else None, "status": status,
        "action": body.action if keep_action else "",
        "action_date": body.action_date if keep_action else None,
        "action_note": body.action_note if keep_action else "",
    }


@app.put("/api/months/{ym}/items/{item_id}")
def save_entry(ym: str, item_id: int, body: EntryIn, user: str = Depends(actor)):
    ym = check_ym(ym)
    with db.connect() as con:
        item = get_item(con, item_id)
        if item["expiry_mode"] == "date" and not body.expiry_date:
            raise HTTPException(400, "期限の日付を入れてください")
        if item["expiry_mode"] == "mfg" and not body.mfg_date:
            raise HTTPException(400, "製造日を入れてください")
        new = build_entry(item, ym, body)
        before = db.row_dict(con.execute("SELECT * FROM entries WHERE ym=? AND item_id=?", (ym, item_id)).fetchone())
        new.update(counted_by=user, counted_at=db.now(), counted_on=check_counted_on(body.counted_on, before))
        cols = list(new)
        con.execute(
            f"INSERT INTO entries (ym, item_id, {', '.join(cols)}) VALUES (?, ?, {', '.join('?' * len(cols))})"
            f" ON CONFLICT(ym, item_id) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in cols)}",
            [ym, item_id, *new.values()],
        )
        after = db.row_dict(con.execute("SELECT * FROM entries WHERE ym=? AND item_id=?", (ym, item_id)).fetchone())
        db.record(con, user, "entry", after["id"], "更新" if before else "入力",
                  f"{ym} {item['code']} {item['name']} {after['total_kg']}kg 期限{after['expiry_date'] or '-'}"
                  f" 棚卸日{after['counted_on']}",
                  before, after)
    return after


@app.delete("/api/months/{ym}/items/{item_id}")
def delete_entry(ym: str, item_id: int, user: str = Depends(actor)):
    ym = check_ym(ym)
    with db.connect() as con:
        item = get_item(con, item_id)
        before = db.row_dict(con.execute("SELECT * FROM entries WHERE ym=? AND item_id=?", (ym, item_id)).fetchone())
        if not before:
            raise HTTPException(404, "入力がありません")
        con.execute("DELETE FROM entries WHERE id=?", (before["id"],))
        db.record(con, user, "entry", before["id"], "取消", f"{ym} {item['code']} {item['name']}", before, None)
    return {"ok": True}


class ExpectedIn(BaseModel):
    kg: float | None = Field(None, ge=0)  # None=予想在庫を消す


@app.put("/api/months/{ym}/items/{item_id}/expected")
def save_expected(ym: str, item_id: int, body: ExpectedIn, user: str = Depends(actor)):
    ym = check_ym(ym)
    with db.connect() as con:
        item = get_item(con, item_id)
        before = db.row_dict(con.execute("SELECT * FROM expected WHERE ym=? AND item_id=?", (ym, item_id)).fetchone())
        if body.kg is None:
            if before:
                con.execute("DELETE FROM expected WHERE id=?", (before["id"],))
                db.record(con, user, "expected", before["id"], "取消", f"{ym} {item['code']} {item['name']}", before, None)
            return {"kg": None}
        con.execute(
            "INSERT INTO expected (ym, item_id, kg, set_by, set_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(ym, item_id) DO UPDATE SET kg=excluded.kg, set_by=excluded.set_by, set_at=excluded.set_at",
            (ym, item_id, body.kg, user, db.now()),
        )
        after = db.row_dict(con.execute("SELECT * FROM expected WHERE ym=? AND item_id=?", (ym, item_id)).fetchone())
        db.record(con, user, "expected", after["id"], "更新" if before else "入力",
                  f"{ym} {item['code']} {item['name']} 予想{after['kg']}kg", before, after)
    return after


class ActionIn(BaseModel):
    action: str = ""
    action_date: str | None = None
    action_note: str = ""


@app.put("/api/months/{ym}/items/{item_id}/action")
def save_action(ym: str, item_id: int, body: ActionIn, user: str = Depends(actor)):
    ym = check_ym(ym)
    with db.connect() as con:
        item = get_item(con, item_id)
        before = db.row_dict(con.execute("SELECT * FROM entries WHERE ym=? AND item_id=?", (ym, item_id)).fetchone())
        if not before:
            raise HTTPException(404, "先に棚卸を入力してください")
        con.execute("UPDATE entries SET action=?, action_date=?, action_note=? WHERE id=?",
                    (body.action, body.action_date or None, body.action_note, before["id"]))
        after = db.row_dict(con.execute("SELECT * FROM entries WHERE id=?", (before["id"],)).fetchone())
        db.record(con, user, "entry", before["id"], "対応", f"{ym} {item['code']} {item['name']} → {body.action}",
                  before, after)
    return after


@app.get("/api/months/{ym}/alerts")
def alerts(ym: str):
    ym = check_ym(ym)
    with db.connect() as con:
        rows = [r for r in month_items(con, ym) if r["status"] in ("warn", "expired")]
    rows.sort(key=lambda r: r["expiry_date"] or "")
    return {"items": rows}


# ---------- 確認（承認） ----------

class ApprovalIn(BaseModel):
    role: str


@app.post("/api/months/{ym}/categories/{category_id}/approvals")
def approve(ym: str, category_id: int, body: ApprovalIn, user: str = Depends(actor)):
    ym = check_ym(ym)
    if body.role not in ROLES:
        raise HTTPException(400, "確認区分が不正です")
    with db.connect() as con:
        cat = category_name(con, category_id)
        rows = month_items(con, ym, category_id)
        crosses = sum(1 for r in rows if r["status"] in ("warn", "expired"))
        detail = f"×{crosses}件 識別{'有' if crosses else '無'}" if body.role == "担当者" else ""
        try:
            con.execute("INSERT INTO approvals (ym, category_id, role, staff_name, detail, at) VALUES (?,?,?,?,?,?)",
                        (ym, category_id, body.role, user, detail, db.now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "すでに確認済みです")
        db.record(con, user, "approval", category_id, "確認", f"{ym} {cat} {body.role} {detail}".strip())
    return {"ok": True}


@app.delete("/api/months/{ym}/categories/{category_id}/approvals/{role}")
def unapprove(ym: str, category_id: int, role: str, user: str = Depends(actor)):
    ym = check_ym(ym)
    with db.connect() as con:
        before = db.row_dict(con.execute("SELECT * FROM approvals WHERE ym=? AND category_id=? AND role=?",
                                         (ym, category_id, role)).fetchone())
        if not before:
            raise HTTPException(404, "確認がありません")
        con.execute("DELETE FROM approvals WHERE id=?", (before["id"],))
        db.record(con, user, "approval", category_id, "確認取消", f"{ym} {category_name(con, category_id)} {role}",
                  before, None)
    return {"ok": True}


# ---------- Excel出力 ----------

@app.get("/api/months/{ym}/export.xlsx")
def export_xlsx(ym: str):
    ym = check_ym(ym)
    with db.connect() as con:
        data = export.build(con, ym, month_items, ROLES)
    name = f"原料棚卸_{ym}.xlsx"
    return Response(
        data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@app.get("/api/months/{ym}/expected.xlsx")
def expected_template(ym: str):
    ym = check_ym(ym)
    with db.connect() as con:
        data = expected_xlsx.build_template(ym, month_items(con, ym))
    name = f"予想在庫_{ym}.xlsx"
    return Response(
        data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


# ---------- 管理者 ----------

@app.post("/api/admin/login")
def admin_login(_: str = Depends(admin)):
    return {"ok": True}


class ItemIn(BaseModel):
    code: str
    name: str
    category_id: int
    storage: str = "常温"
    case_weight: float | None = None
    pallet_cases: float | None = Field(None, gt=0)
    expiry_mode: Literal["date", "mfg", "none"] = "date"
    mfg_months: int | None = None
    note: str = ""
    sort: int = 0
    active: bool = True


ITEM_LABEL = {"code": "コード", "name": "品名", "category_id": "分類", "storage": "保管", "case_weight": "ケース重量",
              "pallet_cases": "1パレットのケース数",
              "expiry_mode": "期限の種類", "mfg_months": "製造日からの月数", "note": "メモ", "sort": "並び順",
              "active": "有効"}


def show_value(v) -> str:
    if v is None or v == "":
        return "未設定"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def diff_summary(before: dict, after: dict, labels: dict) -> str:
    parts = [f"{labels[k]}: {show_value(before.get(k))} → {show_value(after.get(k))}"
             for k in labels if before.get(k) != after.get(k)]
    return " / ".join(parts)


def clean_item(body: ItemIn) -> dict:
    d = body.model_dump()
    d["code"], d["name"] = d["code"].strip(), d["name"].strip()
    if not d["code"] or not d["name"]:
        raise HTTPException(400, "コードと品名は必須です")
    if d["storage"] not in STORAGES:
        raise HTTPException(400, "保管の値が不正です")
    if d["expiry_mode"] == "mfg" and not d["mfg_months"]:
        raise HTTPException(400, "製造日からの月数を入れてください")
    d["active"] = 1 if d["active"] else 0
    return d


@app.post("/api/admin/months/{ym}/expected/import")
async def import_expected(ym: str, request: Request, user: str = Depends(admin)):
    """予想在庫のExcelを取り込む。1件でもエラーがあれば何も書き込まない。"""
    ym = check_ym(ym)
    data = await request.body()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "ファイルが大きすぎます（10MBまで）")
    rows, errors = expected_xlsx.parse(data)
    with db.connect() as con:
        items = {r["code"]: dict(r) for r in con.execute("SELECT id, code, name FROM items")}
        errors += [f"{line}行目：コード {code} は品目マスタにありません" for code, (line, _) in rows.items() if code not in items]
        if errors:
            return {"ok": False, "errors": errors}
        current = {r["item_id"]: dict(r) for r in con.execute("SELECT * FROM expected WHERE ym=?", (ym,))}
        changed = 0
        for code, (_, kg) in rows.items():
            item = items[code]
            before = current.get(item["id"])
            if before and before["kg"] == kg:
                continue
            con.execute(
                "INSERT INTO expected (ym, item_id, kg, set_by, set_at) VALUES (?,?,?,?,?)"
                " ON CONFLICT(ym, item_id) DO UPDATE SET kg=excluded.kg, set_by=excluded.set_by, set_at=excluded.set_at",
                (ym, item["id"], kg, user, db.now()),
            )
            after = db.row_dict(con.execute("SELECT * FROM expected WHERE ym=? AND item_id=?", (ym, item["id"])).fetchone())
            db.record(con, user, "expected", after["id"], "取込",
                      f"{ym} {code} {item['name']} 予想{kg}kg", before, after)
            changed += 1
    return {"ok": True, "rows": len(rows), "changed": changed, "errors": []}


@app.get("/api/admin/items")
def admin_items(_: str = Depends(admin)):
    with db.connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT i.*, c.name AS category_name FROM items i JOIN categories c ON c.id=i.category_id"
            " ORDER BY c.sort, i.sort, i.code")]


@app.post("/api/admin/items")
def create_item(body: ItemIn, user: str = Depends(admin)):
    d = clean_item(body)
    with db.connect() as con:
        if con.execute("SELECT 1 FROM items WHERE code=?", (d["code"],)).fetchone():
            raise HTTPException(409, "同じコードの品目があります")
        cur = con.execute(f"INSERT INTO items ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})", list(d.values()))
        after = db.row_dict(con.execute("SELECT * FROM items WHERE id=?", (cur.lastrowid,)).fetchone())
        db.record(con, user, "item", cur.lastrowid, "追加", f"{d['code']} {d['name']}", None, after)
    return after


@app.put("/api/admin/items/{item_id}")
def update_item(item_id: int, body: ItemIn, user: str = Depends(admin)):
    d = clean_item(body)
    with db.connect() as con:
        before = get_item(con, item_id)
        if con.execute("SELECT 1 FROM items WHERE code=? AND id<>?", (d["code"], item_id)).fetchone():
            raise HTTPException(409, "同じコードの品目があります")
        con.execute(f"UPDATE items SET {', '.join(f'{k}=?' for k in d)} WHERE id=?", [*d.values(), item_id])
        after = get_item(con, item_id)
        change = diff_summary(before, after, ITEM_LABEL)
        if change:
            action = "終売" if before["active"] and not after["active"] else "変更"
            db.record(con, user, "item", item_id, action, f"{after['code']} {after['name']}：{change}", before, after)
    return after


@app.get("/api/admin/items.xlsx")
def export_items(_: str = Depends(admin)):
    with db.connect() as con:
        cats = [dict(r) for r in con.execute("SELECT * FROM categories ORDER BY sort")]
        items = [dict(r) for r in con.execute(
            "SELECT i.* FROM items i JOIN categories c ON c.id=i.category_id ORDER BY c.sort, i.sort, i.code")]
    data = items_xlsx.build(items, cats, STORAGES)
    name = f"品目マスタ_{date.today():%Y%m%d}.xlsx"
    return Response(
        data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@app.post("/api/admin/items/import")
async def import_items(request: Request, apply: bool = False, user: str = Depends(admin)):
    """品目マスタのExcelを取り込む。apply=false なら変更内容を返すだけ。1件でもエラーがあれば何も書き込まない。"""
    data = await request.body()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "ファイルが大きすぎます（10MBまで）")
    with db.connect() as con:
        cats = [dict(r) for r in con.execute("SELECT * FROM categories ORDER BY sort")]
        rows, errors = items_xlsx.parse(data, cats, STORAGES)
        current = {r["code"]: dict(r) for r in con.execute("SELECT * FROM items")}
        cat_name = {c["id"]: items_xlsx.category_label(c) for c in cats}
        plan = []  # (行, 変更前 or None, 変更後)
        for row in rows:
            before = current.get(row["code"])
            base = ({k: before[k] for k in ItemIn.model_fields} if before
                    else {"code": row["code"], "storage": "常温", "expiry_mode": "date", "note": "", "sort": 0, "active": True})
            merged = {**base, **row["values"]}
            if not before and not {"name", "category_id"} <= merged.keys():
                errors.append(f"{row['line']}行目（{row['code']}）：新しい品目には品名と分類が必要です")
                continue
            try:
                d = clean_item(ItemIn(**merged))
            except HTTPException as e:
                errors.append(f"{row['line']}行目（{row['code']}）：{e.detail}")
                continue
            except ValidationError:
                errors.append(f"{row['line']}行目（{row['code']}）：入力内容を確認してください")
                continue
            if before is None or any(before[k] != d[k] for k in d):
                plan.append((row, before, d))
        if errors:
            return {"ok": False, "rows": len(rows), "errors": errors, "changes": []}

        def show(d: dict) -> dict:
            return {**d, "category_id": cat_name.get(d["category_id"], d["category_id"])}

        changes = [{"line": row["line"], "code": d["code"], "name": d["name"], "kind": "変更" if before else "追加",
                    "detail": diff_summary(show(before), show(d), ITEM_LABEL) if before else ""}
                   for row, before, d in plan]
        if apply:
            for row, before, d in plan:
                if before:
                    con.execute(f"UPDATE items SET {', '.join(f'{k}=?' for k in d)} WHERE id=?", [*d.values(), before["id"]])
                    after = get_item(con, before["id"])
                    action = "終売" if before["active"] and not after["active"] else "変更"
                    db.record(con, user, "item", before["id"], f"取込{action}",
                              f"{after['code']} {after['name']}：{diff_summary(show(before), show(after), ITEM_LABEL)}",
                              before, after)
                else:
                    cur = con.execute(f"INSERT INTO items ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})", list(d.values()))
                    after = get_item(con, cur.lastrowid)
                    db.record(con, user, "item", cur.lastrowid, "取込追加", f"{d['code']} {d['name']}", None, after)
    return {"ok": True, "applied": apply, "rows": len(rows), "errors": [], "changes": changes,
            "added": sum(c["kind"] == "追加" for c in changes), "changed": sum(c["kind"] == "変更" for c in changes)}


class CategoryIn(BaseModel):
    grp: str
    name: str
    locations: Literal["split", "single"] = "split"
    sort: int = 0
    active: bool = True


CATEGORY_LABEL = {"grp": "グループ", "name": "分類名", "locations": "置き場所", "sort": "並び順", "active": "有効"}


@app.get("/api/admin/categories")
def admin_categories(_: str = Depends(admin)):
    with db.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM categories ORDER BY sort")]


@app.post("/api/admin/categories")
def create_category(body: CategoryIn, user: str = Depends(admin)):
    d = body.model_dump()
    d["active"] = 1 if d["active"] else 0
    with db.connect() as con:
        cur = con.execute(f"INSERT INTO categories ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})", list(d.values()))
        after = db.row_dict(con.execute("SELECT * FROM categories WHERE id=?", (cur.lastrowid,)).fetchone())
        db.record(con, user, "category", cur.lastrowid, "追加", f"{d['grp']} / {d['name']}", None, after)
    return after


@app.put("/api/admin/categories/{category_id}")
def update_category(category_id: int, body: CategoryIn, user: str = Depends(admin)):
    d = body.model_dump()
    d["active"] = 1 if d["active"] else 0
    with db.connect() as con:
        before = db.row_dict(con.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone())
        if not before:
            raise HTTPException(404, "分類がありません")
        con.execute(f"UPDATE categories SET {', '.join(f'{k}=?' for k in d)} WHERE id=?", [*d.values(), category_id])
        after = db.row_dict(con.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone())
        change = diff_summary(before, after, CATEGORY_LABEL)
        if change:
            db.record(con, user, "category", category_id, "変更", f"{after['name']}：{change}", before, after)
    return after


class StaffIn(BaseModel):
    name: str
    sort: int = 0
    active: bool = True


@app.get("/api/admin/staff")
def admin_staff(_: str = Depends(admin)):
    with db.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM staff ORDER BY sort, name")]


@app.post("/api/admin/staff")
def create_staff(body: StaffIn, user: str = Depends(admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "名前を入れてください")
    with db.connect() as con:
        if con.execute("SELECT 1 FROM staff WHERE name=?", (name,)).fetchone():
            raise HTTPException(409, "同じ名前が登録されています")
        cur = con.execute("INSERT INTO staff (name, sort, active) VALUES (?,?,?)", (name, body.sort, int(body.active)))
        db.record(con, user, "staff", cur.lastrowid, "追加", name)
    return {"ok": True}


@app.put("/api/admin/staff/{staff_id}")
def update_staff(staff_id: int, body: StaffIn, user: str = Depends(admin)):
    with db.connect() as con:
        before = db.row_dict(con.execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone())
        if not before:
            raise HTTPException(404, "登録がありません")
        con.execute("UPDATE staff SET name=?, sort=?, active=? WHERE id=?",
                    (body.name.strip(), body.sort, int(body.active), staff_id))
        after = db.row_dict(con.execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone())
        change = diff_summary(before, after, {"name": "名前", "sort": "並び順", "active": "有効"})
        if change:
            db.record(con, user, "staff", staff_id, "変更", f"{after['name']}：{change}", before, after)
    return {"ok": True}


@app.get("/api/admin/history")
def history(entity: str | None = None, q: str = "", limit: int = 200, _: str = Depends(admin)):
    sql = "SELECT id, at, actor, entity, entity_id, action, summary FROM history WHERE 1=1"
    args: list = []
    if entity:
        sql += " AND entity=?"
        args.append(entity)
    if q:
        sql += " AND (summary LIKE ? OR actor LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(min(limit, 1000))
    with db.connect() as con:
        return [dict(r) for r in con.execute(sql, args)]


# ---------- 画面 ----------

class NoCacheStatic(StaticFiles):
    """更新したJS/CSSがタブレットに古いまま残らないよう、毎回サーバーに確認させる（変わっていなければ304）。"""

    async def get_response(self, path, scope):
        res = await super().get_response(path, scope)
        res.headers["Cache-Control"] = "no-cache"
        return res


app.mount("/static", NoCacheStatic(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
