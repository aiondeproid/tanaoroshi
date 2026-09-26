"""予想在庫のExcel（ひな形の出力と取り込み）。"""
import io
import re
import unicodedata
import zipfile

from openpyxl import Workbook, load_workbook

from .export import BORDER, CENTER, HEAD_FILL, cell

HEADERS = ["コード", "品名", "分類", "保管", "予想在庫(kg)"]
WIDTHS = [12, 36, 18, 8, 14]


def norm(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(v))).strip()


def norm_code(v) -> str:
    """tools/import_master.py と同じ正規化。Excelで数値になって消えた先頭の0も戻す。"""
    c = norm(v).upper()
    return c.zfill(5) if c.isdigit() and len(c) < 5 else c


def build_template(ym: str, rows: list[dict]) -> bytes:
    y, m = map(int, ym.split("-"))
    wb = Workbook()
    ws = wb.active
    ws.title = "予想在庫"
    cell(ws, 1, 1, f"{y}年{m}月 予想在庫")
    cell(ws, 2, 1, "「予想在庫(kg)」の列だけ入れて、管理画面の「予想在庫」タブから取り込んでください。空欄の行は変更しません。")
    for c, (h, w) in enumerate(zip(HEADERS, WIDTHS), 1):
        cell(ws, 3, c, h, fill=HEAD_FILL, border=BORDER, alignment=CENTER)
        ws.column_dimensions[chr(64 + c)].width = w
    for r, it in enumerate(rows, 4):
        vals = [it["code"], it["name"], it["category_name"], it["storage"], it["expected_kg"]]
        for c, v in enumerate(vals, 1):
            cell(ws, r, c, v, border=BORDER)
        ws.cell(row=r, column=1).number_format = "@"  # コードの先頭の0が消えないよう文字列にする
        ws.cell(row=r, column=5).number_format = "0.###"
    ws.freeze_panes = "A4"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def parse(data: bytes) -> tuple[dict[str, tuple[int, float]], list[str]]:
    """{コード: (行番号, kg)} とエラーの一覧を返す。予想在庫が空欄の行は無視する。"""
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except (zipfile.BadZipFile, KeyError, OSError):
        return {}, ["Excelファイル（.xlsx）を選んでください。古い .xls は、Excelで .xlsx に保存し直してください"]
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    code_col = kg_col = header = None
    for i, row in enumerate(rows[:20]):
        texts = [norm(v) if v is not None else "" for v in row]
        codes = [c for c, t in enumerate(texts) if t == "コード"]
        kgs = [c for c, t in enumerate(texts) if "予想" in t]
        if codes and kgs:
            code_col, kg_col, header = codes[0], kgs[0], i
            break
    if header is None:
        return {}, ["見出しの行が見つかりません。「コード」と「予想在庫(kg)」の列がある表にしてください"]

    out: dict[str, tuple[int, float]] = {}
    errors: list[str] = []
    for i, row in enumerate(rows[header + 1:], header + 2):
        get = lambda c: row[c] if c < len(row) else None  # noqa: E731
        raw_code, raw_kg = get(code_col), get(kg_col)
        if raw_kg is None or norm(raw_kg) == "":
            continue
        code = norm_code(raw_code) if raw_code is not None else ""
        if not code:
            errors.append(f"{i}行目：コードが空です")
            continue
        if isinstance(raw_kg, (int, float)):
            kg = float(raw_kg)
        else:
            try:
                kg = float(norm(raw_kg).replace(",", "").removesuffix("kg").removesuffix("KG").strip())
            except ValueError:
                errors.append(f"{i}行目（{code}）：予想在庫「{raw_kg}」が数字ではありません")
                continue
        if kg < 0:
            errors.append(f"{i}行目（{code}）：予想在庫がマイナスです")
            continue
        if code in out:
            errors.append(f"{i}行目（{code}）：{out[code][0]}行目と同じコードです")
            continue
        out[code] = (i, round(kg, 3))
    return out, errors
