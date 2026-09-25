"""期限判定と重量計算。画面側 (static/app.js) にも同じ計算があるので、変えるときは両方直すこと。"""
import calendar
from datetime import date

# 確認月の月末から何ヶ月あればOKとするか
WARN_MONTHS = 1

STATUS_LABEL = {"ok": "レ", "warn": "×", "expired": "×", "none": ""}


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    last = calendar.monthrange(y, m)[1]
    # 月末から数える場合は加算後も月末にそろえる
    if d.day == calendar.monthrange(d.year, d.month)[1]:
        return date(y, m, last)
    return date(y, m, min(d.day, last))


def month_end(ym: str) -> date:
    y, m = map(int, ym.split("-"))
    return date(y, m, calendar.monthrange(y, m)[1])


def ok_limit(ym: str) -> date:
    """この日以降の期限なら「レ」。"""
    return add_months(month_end(ym), WARN_MONTHS)


def expiry_from_mfg(mfg: date, months: int) -> date:
    return add_months(mfg, months)


def judge(expiry: date | None, ym: str, ref: date) -> str:
    """ok=1ヶ月以上あり / warn=1ヶ月未満 / expired=ref時点で期限切れ / none=期限未入力"""
    if expiry is None:
        return "none"
    if expiry < ref:
        return "expired"
    if expiry < ok_limit(ym):
        return "warn"
    return "ok"


def total_kg(case_weight: float | None, cases: float, loose_kg: float) -> float:
    return round((cases or 0) * (case_weight or 0) + (loose_kg or 0), 3)
