"""棚卸結果を紙の管理表に近いレイアウトの .xlsx にする。"""
import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import logic

THIN = Side(style="thin", color="808080")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="D9E1F2")
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
EXPIRED_FILL = PatternFill("solid", fgColor="F8CBAD")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
WRAP = Alignment(vertical="center", wrap_text=True)
STATUS_TEXT = {"ok": "レ", "warn": "×（{}ヶ月以内）", "expired": "×（期限切れ）", "none": ""}
MODE_NOTE = {"none": "期限指定無し"}


def jdate(s: str | None) -> str:
    if not s:
        return ""
    d = date.fromisoformat(s)
    return f"{d.year}/{d.month}/{d.day}"


def counted_range(items) -> str:
    days = sorted({x["counted_on"] for x in items if x["counted_on"]})
    if not days:
        return "未実施"
    return jdate(days[0]) if len(days) == 1 else f"{jdate(days[0])}〜{jdate(days[-1])}"


def safe_title(name: str, used: set) -> str:
    t = "".join(ch for ch in name if ch not in '[]:*?/\\')[:28] or "分類"
    base, n = t, 2
    while t in used:
        t = f"{base[:25]}_{n}"
        n += 1
    used.add(t)
    return t


def status_text(it) -> str:
    return STATUS_TEXT[it["status"]].format(it["warn_months"])


def action_text(it) -> str:
    """分類のシートの「対応」欄。実施済みなら実施日と確認者も書く。"""
    if not it["action"]:
        return ""
    if it["action_done_by"]:
        return f"{it['action']}\n(実施済 {jdate(it['action_done_on'])} {it['action_done_by']})"
    return f"{it['action']}\n(未実施)"


def rule_text(ym: str, months: int) -> str:
    me, limit = logic.month_end(ym), logic.ok_limit(ym, months)
    return (f"賞味期限管理：確認月の月末（{me.month}/{me.day}）から{months}ヶ月"
            f"（{limit.year}/{limit.month}/{limit.day}）を超えてあるか確認　・超えてあり：レ　・{limit.month}/{limit.day}まで：×")


def cell(ws, r, c, v, **style):
    x = ws.cell(row=r, column=c, value=v)
    for k, val in style.items():
        setattr(x, k, val)
    return x


def item_sheet(ws, ym: str, title: str, items, split: bool, with_cat: bool = False,
               warn_months: int | None = None, approvals=()):
    """品目の一覧シート。with_cat=True なら先頭に分類の列を足す（コード順でまとめるとき）。"""
    y, m = map(int, ym.split("-"))
    off = 1 if with_cat else 0
    cell(ws, 1, 1, f"在庫賞味期限管理（{title}）", font=Font(size=14, bold=True))
    cell(ws, 2, 1, f"棚卸月：{y}年{m}月　　棚卸日：{counted_range(items)}　　出力日：{date.today():%Y/%m/%d}")
    if warn_months is not None:
        cell(ws, 3, 1, rule_text(ym, warn_months))
    else:
        months = sorted({x["warn_months"] for x in items})
        cell(ws, 3, 1, "　".join(rule_text(ym, k) for k in months) if len(months) == 1
             else "賞味期限管理：分類ごとの月数で判定（判定の欄を参照）")

    # 確認欄（右上）
    start = (13 if split else 10) + off
    for i, (role, a) in enumerate(approvals):
        cell(ws, 1, start + i, role, fill=HEAD_FILL, border=BORDER, alignment=CENTER, font=Font(bold=True))
        txt = f"{a['staff_name']}\n{a['at'][:10]}" + (f"\n{a['detail']}" if a and a["detail"] else "") if a else ""
        cell(ws, 2, start + i, txt, border=BORDER, alignment=CENTER)
    if approvals:
        ws.row_dimensions[2].height = 45

    loc_heads = (["資材室", "", "倉庫/パレット", "", ""] if split else ["在庫", ""])
    heads = [*(["分類"] if with_cat else []), "コード", "品名", "保管", "ケース重量(kg)", *loc_heads, "総重量(kg)", "区分",
             "賞味期限/使用期限", "チェック(レ・×)", "対応", "対応メモ", "担当者", "棚卸日"]
    loc_sub = ["ケース", "端数kg", "パレット", "ケース", "端数kg"] if split else ["ケース", "端数kg"]
    sub = [*([""] if with_cat else []), "", "", "", "", *loc_sub, "", "", "", "", "", "", "", ""]
    for i, (h, s) in enumerate(zip(heads, sub), 1):
        cell(ws, 5, i, h, fill=HEAD_FILL, border=BORDER, alignment=CENTER, font=Font(bold=True))
        cell(ws, 6, i, s, fill=HEAD_FILL, border=BORDER, alignment=CENTER, font=Font(bold=True))
    ws.merge_cells(start_row=5, start_column=5 + off, end_row=5, end_column=6 + off)
    if split:
        ws.merge_cells(start_row=5, start_column=7 + off, end_row=5, end_column=9 + off)
    for i, h in enumerate(heads, 1):
        if h and not sub[i - 1]:
            ws.merge_cells(start_row=5, start_column=i, end_row=6, end_column=i)

    wrap_cols = {2 + off, (15 if split else 12) + off}
    r = 7
    for it in items:
        entered = it["entry_id"] is not None
        if not entered:
            loc = [None] * len(loc_sub)
        elif split and it["locations"] != "split":
            # まとめたシートで保管場所を分けない分類は「資材室」の欄に書く
            loc = [it["room_cases"], it["room_kg"], None, None, None]
        elif split:
            loc = [it["room_cases"], it["room_kg"], it["wh_pallets"], it["wh_cases"], it["wh_kg"]]
        else:
            loc = [it["room_cases"], it["room_kg"]]
        expiry = jdate(it["expiry_date"]) or (("在庫ゼロ" if not it["total_kg"] else MODE_NOTE.get(it["expiry_mode"], ""))
                                              if entered else "")
        if it["mfg_date"]:
            mfg = f"製造日{jdate(it['mfg_date'])}"
            expiry = f"{expiry}\n({mfg})" if expiry else mfg
        name = it["name"] + ("（終売）" if not it["active"] else "")
        vals = [*([it["category_name"]] if with_cat else []),
                it["code"], name, it["storage"], it["entry_case_weight"] if entered else it["case_weight"], *loc,
                it["total_kg"] if entered else None, it["expiry_kind"] if entered else "", expiry,
                status_text(it) if entered else "未入力", action_text(it), it["action_note"] or "",
                it["counted_by"] or "", jdate(it["counted_on"])]
        fill = {"warn": WARN_FILL, "expired": EXPIRED_FILL}.get(it["status"]) if entered else None
        for i, v in enumerate(vals, 1):
            x = cell(ws, r, i, v, border=BORDER, alignment=WRAP if i in wrap_cols else CENTER)
            if fill:
                x.fill = fill
        r += 1

    widths = [*([14] if with_cat else []), 10, 30, 6, 9, *([7, 8, 7, 7, 8] if split else [7, 8]),
              10, 5, 13, 11, 14, 22, 9, 11]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=7, column=3 + off).coordinate
    ws.print_title_rows = "5:6"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def build(con, ym: str, month_items, roles: list[str], order: str = "category") -> bytes:
    """order="category"：分類ごとのシート、"code"：全品目をコード順に1シートにまとめる。"""
    y, m = map(int, ym.split("-"))
    rows = month_items(con, ym)
    cats = [dict(r) for r in con.execute("SELECT * FROM categories WHERE active=1 ORDER BY sort")]
    appr = {}
    for a in con.execute("SELECT * FROM approvals WHERE ym=?", (ym,)):
        appr[(a["category_id"], a["role"])] = a

    wb = Workbook()
    used: set = set()

    # 1枚目：期限警告の一覧
    ws = wb.active
    ws.title = safe_title("期限警告一覧", used)
    cell(ws, 1, 1, f"期限警告一覧（{y}年{m}月 棚卸）", font=Font(size=14, bold=True))
    # 分類で月数が違うときは月数ごとに1行ずつ書く
    by_months: dict[int, list[str]] = {}
    for c in cats:
        by_months.setdefault(c["warn_months"], []).append(c["name"])
    # 確認欄（右上）
    alert_appr = {a["role"]: a for a in con.execute("SELECT * FROM alert_approvals WHERE ym=?", (ym,))}
    for i, role in enumerate(roles):
        a = alert_appr.get(role)
        cell(ws, 1, 11 + i, role, fill=HEAD_FILL, border=BORDER, alignment=CENTER, font=Font(bold=True))
        cell(ws, 2, 11 + i, f"{a['staff_name']}\n{a['at'][:10]}" + (f"\n{a['detail']}" if a["detail"] else "") if a else "",
             border=BORDER, alignment=CENTER)
    ws.row_dimensions[2].height = 45
    for i, (months, names) in enumerate(sorted(by_months.items())):
        cell(ws, 2 + i, 1, rule_text(ym, months) + ("" if len(by_months) == 1 else f"（{'・'.join(names)}）"))
    top = 3 + len(by_months)
    heads = ["分類", "コード", "品名", "総重量(kg)", "区分", "期限", "判定", "対応", "対応予定日", "対応メモ",
             "実施日", "実施確認", "担当者", "棚卸日"]
    for i, h in enumerate(heads, 1):
        cell(ws, top, i, h, fill=HEAD_FILL, border=BORDER, alignment=CENTER, font=Font(bold=True))
    r = first = top + 1
    for it in sorted((x for x in rows if x["status"] in ("warn", "expired")), key=lambda x: x["expiry_date"] or ""):
        vals = [it["category_name"], it["code"], it["name"], it["total_kg"], it["expiry_kind"],
                jdate(it["expiry_date"]), status_text(it), it["action"], jdate(it["action_date"]),
                it["action_note"], jdate(it["action_done_on"]), it["action_done_by"] or ("未実施" if it["action"] else ""),
                it["counted_by"], jdate(it["counted_on"])]
        fill = EXPIRED_FILL if it["status"] == "expired" else WARN_FILL
        for i, v in enumerate(vals, 1):
            cell(ws, r, i, v, border=BORDER, alignment=WRAP, fill=fill)
        r += 1
    if r == first:
        cell(ws, r, 1, "警告はありません")
    for i, w in enumerate([14, 9, 28, 10, 6, 11, 15, 14, 11, 24, 11, 10, 10, 11], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    # 印刷：A4横・横幅を1ページに収め、縦は何ページでも。見出し行は各ページに出し、下にページ番号
    ws.freeze_panes = ws.cell(row=top + 1, column=1).coordinate
    ws.print_area = f"A1:{get_column_letter(len(heads))}{max(r, first)}"
    ws.print_title_rows = f"{top}:{top}"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = ws.page_margins.bottom = 0.6
    ws.oddFooter.center.text = "&P / &N ページ"

    if order == "code":
        # 全分類をコード順に1枚にまとめる（分類の列を足し、保管場所は分けて書ける形にそろえる）
        items = sorted(rows, key=lambda x: (x["code"], x["category_name"]))
        if items:
            ws = wb.create_sheet(safe_title("全品目（コード順）", used))
            item_sheet(ws, ym, "全品目・コード順", items, split=True, with_cat=True)
    else:
        for cat in cats:
            items = [x for x in rows if x["category_id"] == cat["id"]]
            if not items:
                continue
            ws = wb.create_sheet(safe_title(cat["name"], used))
            item_sheet(ws, ym, cat["name"], items, split=cat["locations"] == "split", warn_months=cat["warn_months"],
                       approvals=[(role, appr.get((cat["id"], role))) for role in roles])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
