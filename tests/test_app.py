import io
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
    assert e["action"] == ""  # 問題なければ対応は残さない


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


def test_mfg_expiry(client):
    it = items(client)["01009"]
    assert client.put(f"/api/months/2026-09/items/{it['id']}", headers=USER, json={"room_cases": 1}).status_code == 400
    r = client.put(f"/api/months/2026-09/items/{it['id']}", headers=USER, json={"room_cases": 1, "mfg_date": "2026-08-10"})
    assert r.json()["expiry_date"] == "2027-02-10"


def test_item_change_and_discontinue_are_logged(client):
    it = items(client)["04084"]
    body = {"code": "04084", "name": "アペックス1000", "category_id": client.cat_id, "case_weight": 8, "active": False}
    assert client.put(f"/api/admin/items/{it['id']}", headers=ADMIN, json=body).status_code == 200
    hist = client.get("/api/admin/history?entity=item", headers=ADMIN).json()
    assert hist[0]["action"] == "終売"
    assert "ケース重量: 7.0 → 8.0" in hist[0]["summary"]
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


def test_export_xlsx(client):
    r = client.get("/api/months/2026-09/export.xlsx")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["期限警告一覧", "植蛋"]
    ws = wb["植蛋"]
    codes = [ws.cell(row=r, column=1).value for r in range(7, ws.max_row + 1)]
    assert set(codes) == {"04084", "01009"}
