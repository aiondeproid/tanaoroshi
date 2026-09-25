"""既存の在庫賞味期限管理表(.xls)から分類と品目マスタを取り込む。

使い方:  .venv\\Scripts\\python tools\\import_master.py [xlsのあるフォルダ]
すでに登録済みのコードは上書きしない（管理画面で直した内容を守るため）。
"""
import re
import sys
import unicodedata
from pathlib import Path

import xlrd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402

# (ファイル名, シート名, グループ, 分類名, 置き場所)
SOURCES = [
    ("◆タレ液体資材在庫賞味期限管理.xls", "タレ液体", "タレ液体資材", "タレ液体", "split"),
    ("◆マゼラー（タレ兼用）植蛋　在庫賞味期限管理表.xls", "マゼラータレ兼用", "マゼラー・植蛋", "マゼラータレ兼用", "split"),
    ("◆マゼラー（タレ兼用）植蛋　在庫賞味期限管理表.xls", "新 植たん用", "マゼラー・植蛋", "植蛋", "split"),
    ("◆マゼラー（タレ兼用）植蛋　在庫賞味期限管理表.xls", "新 マゼラー ", "マゼラー・植蛋", "マゼラー", "split"),
    ("◆原料肉　在庫賞味期限管理表.xls", "棚卸チェック表", "原料肉", "原料肉", "single"),
    ("◆粉体資材　 在庫　賞味期限管理.xls", "新　資材室（粉体）① ", "粉体資材", "粉体①", "split"),
    ("◆粉体資材　 在庫　賞味期限管理.xls", "新②", "粉体資材", "粉体②", "split"),
    ("◆粉体資材　 在庫　賞味期限管理.xls", "新③", "粉体資材", "粉体③", "split"),
    ("◆粉体資材　 在庫　賞味期限管理.xls", "新④", "粉体資材", "粉体④", "split"),
    ("◆粉体資材　 在庫　賞味期限管理.xls", "新⑤", "粉体資材", "粉体⑤", "split"),
    ("◆粉体資材　 在庫　賞味期限管理.xls", "新⑥", "粉体資材", "粉体⑥", "split"),
    ("◆粉体資材　 在庫　賞味期限管理.xls", "新⑦", "粉体資材", "粉体⑦", "split"),
    ("◆粉体資材　 在庫　賞味期限管理.xls", "台車 その他⑧", "粉体資材", "粉体⑧ 台車その他", "split"),
    ("◆野菜チーズ玉ねぎ　在庫賞味期限管理.xls", "棚卸チェック表", "野菜チーズ玉ねぎ", "野菜・チーズ・玉ねぎ", "single"),
]

STORAGE = {"常温": "常温", "冷ぞう": "冷蔵", "冷蔵": "冷蔵", "冷凍": "冷凍"}


def norm(v) -> str:
    if isinstance(v, float):
        v = str(int(v)) if v.is_integer() else str(v)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(v))).strip()


def norm_code(v) -> str:
    c = norm(v).upper()
    # 数字だけのコードは5桁ゼロ埋めにそろえる（4084.0 と 04084 を同じにする）
    return c.zfill(5) if c.isdigit() and len(c) < 5 else c


def parse_weight(v) -> tuple[float | None, str]:
    """ケース重量。数値にできなければ None と元の表記を返す。"""
    if isinstance(v, float):
        return v, ""
    s = norm(v).replace("/", "").lower()
    if not s:
        return None, ""
    m = re.fullmatch(r"([\d.]+)\s*kg\s*[x×]\s*(\d+)", s)
    if m:
        return round(float(m[1]) * int(m[2]), 3), f"元の表記: {norm(v)}"
    m = re.search(r"([\d.]+)\s*k", s)
    if m:
        return float(m[1]), f"元の表記: {norm(v)}"
    return None, f"元の表記: {norm(v)}"


def parse_expiry(text: str) -> tuple[str, int | None]:
    t = norm(text)
    if "指定無" in t:
        return "none", None
    m = re.search(r"製造日\s*\+\s*(\d+)\s*(ヶ月|年)", t)
    if m:
        n = int(m[1])
        return "mfg", n * 12 if m[2] == "年" else n
    return "date", None


def read_sheet(path: Path, sheet: str) -> list[dict]:
    sh = xlrd.open_workbook(path).sheet_by_name(sheet)
    out, col = [], None
    for r in range(sh.nrows):
        row = sh.row_values(r)
        texts = [norm(v) for v in row]
        if "コード" in texts:
            col = {name: i for i, name in enumerate(texts) if name}
            col["_code"] = texts.index("コード")
            col["_expiry"] = next((i for i, t in enumerate(texts) if t.startswith("賞味期限") and "チェック" not in t), None)
            col["_storage"] = next((i for i, t in enumerate(texts) if t.startswith("保管")), None)
            col["_weight"] = texts.index("ケース重量") if "ケース重量" in texts else None
            continue
        if col is None:
            continue
        code, name = norm_code(row[col["_code"]]), norm(row[col["_code"] + 1])
        if not code or not name or code.startswith("在庫"):
            continue
        weight, wnote = parse_weight(row[col["_weight"]]) if col["_weight"] is not None else (None, "")
        storage = STORAGE.get(norm(row[col["_storage"]]), "常温") if col["_storage"] is not None else "常温"
        mode, months = parse_expiry(row[col["_expiry"]]) if col["_expiry"] is not None else ("date", None)
        out.append({"code": code, "name": name, "storage": storage, "case_weight": weight,
                    "expiry_mode": mode, "mfg_months": months, "note": wnote})
    return out


def main(folder: Path) -> None:
    db.init()
    added, skipped, notes = 0, 0, []
    with db.connect() as con:
        for sort, (fname, sheet, grp, cname, loc) in enumerate(SOURCES, 1):
            row = con.execute("SELECT id FROM categories WHERE grp=? AND name=?", (grp, cname)).fetchone()
            if row:
                cid = row["id"]
            else:
                cid = con.execute("INSERT INTO categories (grp, name, locations, sort) VALUES (?,?,?,?)",
                                  (grp, cname, loc, sort * 10)).lastrowid
                db.record(con, "初期取り込み", "category", cid, "追加", f"{grp} / {cname}")
            for i, it in enumerate(read_sheet(folder / fname, sheet), 1):
                if con.execute("SELECT 1 FROM items WHERE code=?", (it["code"],)).fetchone():
                    skipped += 1
                    continue
                d = {**it, "category_id": cid, "sort": i * 10}
                iid = con.execute(f"INSERT INTO items ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})",
                                  list(d.values())).lastrowid
                db.record(con, "初期取り込み", "item", iid, "追加", f"{d['code']} {d['name']}（{fname} / {sheet.strip()}）",
                          None, d)
                added += 1
                if d["case_weight"] is None or d["note"]:
                    notes.append(f"  {cname}: {d['code']} {d['name']}  ケース重量={d['case_weight']}  {d['note']}")
    print(f"追加 {added} 件 / 登録済みのため飛ばした {skipped} 件")
    if notes:
        print("ケース重量を確認してほしい品目:")
        print("\n".join(notes))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
