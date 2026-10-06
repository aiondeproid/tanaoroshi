import io
import json
import os
import tempfile
from datetime import date
from pathlib import Path

os.environ["TANAOROSHI_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["TANAOROSHI_ADMIN_PIN"] = "9999"

import openpyxl  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import logic  # noqa: E402
from app.main import app  # noqa: E402

USER = {"X-User": "%E7%94%B0%E4%B8%AD"}  # 田中
ADMIN = {**USER, "X-Admin-Pin": "9999"}


# ---------- 判定ロジック ----------

def test_add_months_keeps_month_end():
    assert logic.add_months(date(2026, 9, 30), 1) == date(2026, 10, 31)
    assert logic.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert logic.add_months(date(2026, 1, 15), 6) == date(2026, 7, 15)
    assert logic.add_months(date(2026, 11, 10), 24) == date(2028, 11, 10)


@pytest.mark.parametrize("expiry, expected", [
    (date(2026, 10, 31), "ok"),       # 月末(9/30)からちょうど1ヶ月
    (date(2026, 10, 30), "warn"),     # 1ヶ月に1日足りない
    (date(2026, 9, 26), "warn"),
    (date(2026, 9, 24), "expired"),
    (None, "none"),
])
def test_judge(expiry, expected):
    assert logic.judge(expiry, "2026-09", date(2026, 9, 25)) == expected


def test_total_kg():
    assert logic.total_kg(10, 3, 4.5) == 34.5
    assert logic.total_kg(None, 3, 4.5) == 4.5


# ---------- API ----------

@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    assert c.post("/api/admin/staff", json={"name": "田中"}, headers=ADMIN).status_code == 200
    cat = c.post("/api/admin/categories", json={"grp": "植蛋", "name": "植蛋"}, headers=ADMIN).json()
    c.post("/api/admin/items", headers=ADMIN, json={"code": "04084", "name": "アペックス1000", "category_id": cat["id"], "case_weight": 7})
    c.post("/api/admin/items", headers=ADMIN, json={"code": "01009", "name": "上白糖", "category_id": cat["id"], "case_weight": 20,
                                                   "expiry_mode": "mfg", "mfg_months": 6})
    c.cat_id = cat["id"]
    return c


def items(client, ym="2026-09"):
    return {i["code"]: i for i in client.get(f"/api/months/{ym}/categories/{client.cat_id}").json()["items"]}


def test_requires_name_and_pin(client):
    assert client.put("/api/months/2026-09/items/1", json={}).status_code == 400
    assert client.get("/api/admin/items", headers=USER).status_code == 403


def test_save_entry_computes_total_and_status(client):
    it = items(client)["04084"]
    far = logic.add_months(date.today(), 3).isoformat()
    r = client.put(f"/api/months/2026-09/items/{it['id']}", headers=USER,
                   json={"room_cases": 2, "room_kg": 1.5, "wh_cases": 1, "wh_kg": 0, "expiry_kind": "賞", "expiry_date": far,
                         "action": "廃棄"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["total_kg"] == 22.5
    assert e["counted_by"] == "田中"
    assert e["counted_on"] == date.today().isoformat()  # 棚卸日は保存した日が自動で入る
    s = client.get("/api/months/2026-09/summary").json()["categories"][str(client.cat_id)]
    assert s["counted_from"] == s["counted_to"] == date.today().isoformat()
    assert e["action"] == ""  # 問題なければ対応は残さない


def test_loose_kg_parts_are_summed_and_kept(client):
    it = items(client)["04084"]
    far = logic.add_months(date.today(), 3).isoformat()
    r = client.put(f"/api/months/2026-09/items/{it['id']}", headers=USER,
                   json={"room_cases": 2, "room_kg_parts": [1.5, 2, 1], "wh_cases": 1, "wh_kg_parts": [0.2, 0, 0],
                         "expiry_kind": "賞", "expiry_date": far})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["room_kg"] == 4.5 and e["wh_kg"] == 0.2
    assert e["total_kg"] == 25.7
    got = items(client)["04084"]
    assert json.loads(got["room_kg_parts"]) == [1.5, 2, 1]
    bad = client.put(f"/api/months/2026-09/items/{it['id']}", headers=USER,
                     json={"room_kg_parts": [1, 1, 1, 1], "expiry_kind": "賞", "expiry_date": far})
    assert bad.status_code == 422


def test_pallets_count_as_cases(client):
    it = items(client, "2026-07")["04084"]
    far = logic.add_months(date.today(), 3).isoformat()
    body = {"room_cases": 1, "wh_pallets": 2, "wh_cases": 3, "wh_kg_parts": [0.5, 0, 0], "expiry_kind": "賞", "expiry_date": far}
    # 1パレットのケース数が未設定ならパレットは入れられない
    r = client.put(f"/api/months/2026-07/items/{it['id']}", headers=USER, json=body)
    assert r.status_code == 400
    master = {k: it[k] for k in ("code", "name", "category_id", "storage", "case_weight", "expiry_mode", "mfg_months", "note", "sort")}
    r = client.put(f"/api/admin/items/{it['id']}", headers=ADMIN, json={**master, "pallet_cases": 40})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/months/2026-07/items/{it['id']}", headers=USER, json=body)
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["wh_pallets"] == 2 and e["pallet_cases"] == 40
    assert e["total_kg"] == (1 + 3 + 2 * 40) * 7 + 0.5
    ws = openpyxl.load_workbook(io.BytesIO(client.get("/api/months/2026-07/export.xlsx").content))["植蛋"]
    assert [ws.cell(row=6, column=c).value for c in range(5, 10)] == ["ケース", "端数kg", "パレット", "ケース", "端数kg"]
    row = [ws.cell(row=r, column=1).value for r in range(7, ws.max_row + 1)].index("04084") + 7
    assert [ws.cell(row=row, column=c).value for c in range(5, 10)] == [1, 0, 2, 3, 0.5]


def test_expired_entry_shows_in_alerts_and_action(client):
    ym = date.today().strftime("%Y-%m")
    it = items(client, ym)["04084"]
    past = date(2020, 1, 1).isoformat()
    r = client.put(f"/api/months/{ym}/items/{it['id']}", headers=USER,
                   json={"room_cases": 1, "expiry_kind": "凍", "expiry_date": past, "action": "廃棄", "action_note": "来週廃棄"})
    assert r.json()["status"] == "expired"
    alerts = client.get(f"/api/months/{ym}/alerts").json()["items"]
    assert [a["code"] for a in alerts] == ["04084"]
    assert alerts[0]["action"] == "廃棄"
    r = client.put(f"/api/months/{ym}/items/{it['id']}/action", headers=USER, json={"action": "移動", "action_note": "A倉庫"})
    assert r.json()["action"] == "移動"


def test_counted_on_can_be_corrected(client):
    it = items(client, "2026-08")["01009"]
    url = f"/api/months/2026-08/items/{it['id']}"
    body = {"room_cases": 1, "mfg_date": "2026-08-10"}
    assert client.put(url, headers=USER, json=body).json()["counted_on"] == date.today().isoformat()
    # あとから別の日に直せる
    assert client.put(url, headers=USER, json={**body, "counted_on": "2026-08-31"}).json()["counted_on"] == "2026-08-31"
    # 棚卸日を送らずに入れ直しても、直した日は変わらない
    assert client.put(url, headers=USER, json={**body, "room_cases": 2}).json()["counted_on"] == "2026-08-31"
    tomorrow = date.fromordinal(date.today().toordinal() + 1).isoformat()
    assert client.put(url, headers=USER, json={**body, "counted_on": tomorrow}).status_code == 400
    assert client.put(url, headers=USER, json={**body, "counted_on": "8月31日"}).status_code == 400
    s = client.get("/api/months/2026-08/summary").json()["categories"][str(client.cat_id)]
    assert s["counted_from"] == s["counted_to"] == "2026-08-31"
    assert "棚卸日2026-08-31" in client.get("/api/admin/history?entity=entry", headers=ADMIN).json()[0]["summary"]


def test_old_db_gets_counted_on(tmp_path, monkeypatch):
    import sqlite3
    from app import db
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, ym TEXT, item_id INTEGER, counted_by TEXT, counted_at TEXT)")
    con.execute("INSERT INTO entries VALUES (1, '2026-09', 1, '田中', '2026-09-24T10:00:00')")
    con.commit()
    con.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init()
    with db.connect() as con:
        assert con.execute("SELECT counted_on FROM entries").fetchone()[0] == "2026-09-24"


def test_mfg_expiry(client):
    it = items(client)["01009"]
    assert client.put(f"/api/months/2026-09/items/{it['id']}", headers=USER, json={"room_cases": 1}).status_code == 400
    r = client.put(f"/api/months/2026-09/items/{it['id']}", headers=USER, json={"room_cases": 1, "mfg_date": "2026-08-10"})
    assert r.json()["expiry_date"] == "2027-02-10"


def test_expected_stock(client):
    it = items(client, "2026-10")["01009"]
    url = f"/api/months/2026-10/items/{it['id']}/expected"
    assert it["expected_kg"] is None
    # 棚卸の入力前でも入れられる
    assert client.put(url, headers=USER, json={"kg": 45.5}).json()["kg"] == 45.5
    assert client.put(url, headers=USER, json={"kg": 40}).status_code == 200
    assert client.put(url, headers=USER, json={"kg": -1}).status_code == 422
    assert items(client, "2026-10")["01009"]["expected_kg"] == 40
    assert items(client, "2026-11")["01009"]["expected_kg"] is None  # 月ごと
    hist = client.get("/api/admin/history?entity=expected", headers=ADMIN).json()
    assert [h["action"] for h in hist[:2]] == ["更新", "入力"]
    assert client.put(url, headers=USER, json={"kg": None}).status_code == 200
    assert items(client, "2026-10")["01009"]["expected_kg"] is None


def xlsx(rows) -> bytes:
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_expected_import_from_template(client):
    url = "/api/admin/months/2026-12/expected/import"
    r = client.get("/api/months/2026-12/expected.xlsx")
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    assert [c.value for c in ws[3]] == ["コード", "品名", "分類", "保管", "予想在庫(kg)"]
    row = next(r for r in range(4, ws.max_row + 1) if ws.cell(row=r, column=1).value == "04084")
    ws.cell(row=row, column=5).value = 12.5
    buf = io.BytesIO()
    ws.parent.save(buf)
    res = client.post(url, headers=ADMIN, content=buf.getvalue()).json()
    assert res == {"ok": True, "rows": 1, "changed": 1, "errors": []}
    assert items(client, "2026-12")["04084"]["expected_kg"] == 12.5
    assert client.get("/api/admin/history?entity=expected", headers=ADMIN).json()[0]["action"] == "取込"
    # 同じ値なら更新しない
    assert client.post(url, headers=ADMIN, content=buf.getvalue()).json()["changed"] == 0


def test_expected_import_rules(client):
    url = "/api/admin/months/2026-12/expected/import"
    assert client.post(url, headers=USER, content=b"").status_code == 403
    # 数値になったコードも5桁に戻す。空欄は無視。「kg」付きの文字も読む
    res = client.post(url, headers=ADMIN, content=xlsx([["メモ"], ["コード", "予想在庫"], [1009, "30kg"], ["04084", None]])).json()
    assert res["ok"] and res["rows"] == 1
    assert items(client, "2026-12")["01009"]["expected_kg"] == 30
    # エラーが1件でもあれば何も書かない
    res = client.post(url, headers=ADMIN, content=xlsx([["コード", "予想在庫(kg)"], ["01009", 99], ["99999", 1], ["04084", "たくさん"]])).json()
    assert not res["ok"] and len(res["errors"]) == 2
    assert items(client, "2026-12")["01009"]["expected_kg"] == 30
    assert not client.post(url, headers=ADMIN, content=b"not excel").json()["ok"]
    assert "見出し" in client.post(url, headers=ADMIN, content=xlsx([["a", "b"]])).json()["errors"][0]


def test_item_change_and_discontinue_are_logged(client):
    it = items(client)["04084"]
    body = {"code": "04084", "name": "アペックス1000", "category_id": client.cat_id, "case_weight": 8, "active": False}
    assert client.put(f"/api/admin/items/{it['id']}", headers=ADMIN, json=body).status_code == 200
    hist = client.get("/api/admin/history?entity=item", headers=ADMIN).json()
    assert hist[0]["action"] == "終売"
    assert "ケース重量: 7 → 8" in hist[0]["summary"]
    # 終売でも、入力済みの月には残る。未入力の月には出ない
    assert "04084" in items(client, "2026-09")
    assert "04084" not in items(client, "2030-01")


def test_approval_flow(client):
    url = f"/api/months/2026-09/categories/{client.cat_id}/approvals"
    assert client.post(url, headers=USER, json={"role": "担当者"}).status_code == 200
    assert client.post(url, headers=USER, json={"role": "担当者"}).status_code == 409
    assert client.post(url, headers=USER, json={"role": "社長"}).status_code == 400
    s = client.get("/api/months/2026-09/summary").json()["categories"][str(client.cat_id)]
    assert s["approvals"] == {"担当者": "田中"}
    assert client.delete(f"{url}/%E6%8B%85%E5%BD%93%E8%80%85", headers=USER).status_code == 200


def test_static_is_not_cached(client):
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"


def test_export_xlsx(client):
    r = client.get("/api/months/2026-09/export.xlsx")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["期限警告一覧", "植蛋"]
    ws = wb["植蛋"]
    codes = [ws.cell(row=r, column=1).value for r in range(7, ws.max_row + 1)]
    assert set(codes) == {"04084", "01009"}
    today = date.today()
    assert f"棚卸日：{today.year}/{today.month}/{today.day}" in ws["A2"].value
    heads = [ws.cell(row=5, column=c).value for c in range(1, ws.max_column + 1)]
    col = heads.index("棚卸日") + 1
    row = codes.index("04084") + 7
    assert ws.cell(row=row, column=col).value == f"{today.year}/{today.month}/{today.day}"


def test_items_excel_round_trip(client):
    assert client.get("/api/admin/items.xlsx", headers=USER).status_code == 403
    r = client.get("/api/admin/items.xlsx", headers=ADMIN)
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb["品目"]
    heads = [c.value for c in ws[3]]
    col = {h: i + 1 for i, h in enumerate(heads)}
    rows = {ws.cell(row=i, column=1).value: i for i in range(4, ws.max_row + 1)}
    assert set(rows) == {"04084", "01009"}
    assert ws.cell(row=rows["01009"], column=col["期限の種類"]).value == "製造日から計算"
    url = "/api/admin/items/import"
    # そのまま戻しても何も変わらない
    buf = io.BytesIO(); wb.save(buf)
    r = client.post(url, headers=ADMIN, content=buf.getvalue()).json()
    assert r["ok"] and r["changes"] == []

    ws.cell(row=rows["01009"], column=col["1パレットのケース数"], value=50)
    ws.cell(row=rows["01009"], column=col["ケース重量(kg)"]).value = None  # 空欄=未設定
    assert ws.cell(row=rows["04084"], column=col["状態"]).value == "終売"  # 前のテストで終売にした
    ws.cell(row=rows["04084"], column=col["状態"], value="有効")
    new = ws.max_row + 1
    for h, v in {"コード": 2001, "品名": "新しい粉", "分類": "植蛋 / 植蛋", "保管": "冷蔵", "期限の種類": "期限指定無し"}.items():
        ws.cell(row=new, column=col[h], value=v)
    buf = io.BytesIO(); wb.save(buf)
    data = buf.getvalue()
    r = client.post(url, headers=ADMIN, content=data).json()  # 確認だけ
    assert r["ok"] and (r["added"], r["changed"]) == (1, 2)
    assert {c["code"]: c["kind"] for c in r["changes"]} == {"01009": "変更", "04084": "変更", "02001": "追加"}
    assert items(client, "2026-06")["01009"]["case_weight"] == 20  # まだ反映しない
    r = client.post(url + "?apply=1", headers=ADMIN, content=data).json()
    assert r["ok"] and r["applied"]
    master = {i["code"]: i for i in client.get("/api/admin/items", headers=ADMIN).json()}
    assert master["01009"]["pallet_cases"] == 50 and master["01009"]["case_weight"] is None
    assert master["04084"]["active"] == 1
    assert master["02001"]["storage"] == "冷蔵" and master["02001"]["expiry_mode"] == "none"
    hist = client.get("/api/admin/history?entity=item", headers=ADMIN).json()
    assert {"取込追加", "取込変更"} <= {h["action"] for h in hist}

    # エラーが1件でもあれば何も変えない
    bad = xlsx([["コード", "品名", "保管", "ケース重量(kg)"], ["01009", "上白糖", "冷蔵", 5], ["01009", "", "常温", None],
                ["9999", "分類なし", "常温", 1], ["02001", "", "地下", "重い"]])
    r = client.post(url + "?apply=1", headers=ADMIN, content=bad).json()
    assert not r["ok"] and len(r["errors"]) == 4
    master2 = {i["code"]: i for i in client.get("/api/admin/items", headers=ADMIN).json()}
    assert master2["01009"] == master["01009"]
