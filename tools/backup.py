"""データベースを日付つきでバックアップし、13ヶ月より古いバックアップを消す。

使い方:  .venv\\Scripts\\python tools\\backup.py [保存先フォルダ]
保存先を省略すると backup_dir.txt の1行目を読む。PCの外（共有フォルダ）を UNC パスで指定すること。
アプリを動かしたままでも安全にコピーできる。同じ日に2回実行すると、その日の分を上書きする。
タスクスケジューラからは backup.bat を実行する。
"""
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import db  # noqa: E402
from app.logic import add_months  # noqa: E402

# 何ヶ月分残すか
KEEP_MONTHS = 13

NAME = re.compile(r"^tanaoroshi_(\d{8})\.db$")


def backup(src: Path, dest_dir: Path, today: date) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"tanaoroshi_{today:%Y%m%d}.db"
    tmp = out.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)
    # ファイルコピーだと WAL に残っている最新の入力が抜けるので、SQLite のバックアップ機能を使う
    s = sqlite3.connect(src, timeout=30)
    d = sqlite3.connect(tmp)
    try:
        s.backup(d)
        result = d.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        d.close()
        s.close()
    if result != "ok":
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"バックアップの検査で異常がありました: {result}")
    tmp.replace(out)
    return out


def prune(dest_dir: Path, today: date) -> list[Path]:
    cutoff = add_months(today, -KEEP_MONTHS)
    removed = []
    for p in sorted(dest_dir.iterdir()):
        m = NAME.match(p.name)
        if not m:
            continue
        try:
            day = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if day < cutoff:
            p.unlink()
            removed.append(p)
    return removed


def read_dest() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    conf = ROOT / "backup_dir.txt"
    if not conf.exists():
        sys.exit("backup_dir.txt がありません。1行目に保存先のフォルダ（UNC パス）を書いてください")
    try:
        text = conf.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = conf.read_text(encoding="cp932")
    lines = [s.strip() for s in text.splitlines() if s.strip() and not s.strip().startswith("#")]
    if not lines:
        sys.exit("backup_dir.txt に保存先のフォルダが書かれていません")
    return Path(lines[0])


def main() -> None:
    dest = read_dest()
    if not db.DB_PATH.exists():
        sys.exit(f"{db.DB_PATH} がありません")
    today = date.today()
    out = backup(db.DB_PATH, dest, today)
    removed = prune(dest, today)
    print(f"{datetime.now():%Y-%m-%d %H:%M} バックアップしました: {out}（古いバックアップを {len(removed)} 件削除）")


if __name__ == "__main__":
    # backup.log に書くとき、エラーの文字コードもそろえる
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
