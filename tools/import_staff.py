"""担当者の名前をテキストファイルからまとめて登録する。

使い方:  .venv\\Scripts\\python tools\\import_staff.py [名簿ファイル]
名簿ファイルは1行に1人。省略すると staff.txt を読む。空行と # で始まる行は読み飛ばす。
並び順はファイルに書いた順。すでに登録済みの名前は変更しない。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402


def read_names(path: Path) -> list[str]:
    names = []
    # Excel やメモ帳で保存したファイルも読めるように、BOM付きUTF-8とShift_JISの両方を試す
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp932")
    for line in text.splitlines():
        name = line.strip()
        if name and not name.startswith("#") and name not in names:
            names.append(name)
    return names


def main(path: Path) -> None:
    if not path.exists():
        sys.exit(f"{path} がありません。1行に1人ずつ名前を書いたファイルを用意してください")
    names = read_names(path)
    db.init()
    added, skipped = [], []
    with db.connect() as con:
        sort = con.execute("SELECT COALESCE(MAX(sort), 0) FROM staff").fetchone()[0]
        for name in names:
            if con.execute("SELECT 1 FROM staff WHERE name=?", (name,)).fetchone():
                skipped.append(name)
                continue
            sort += 10
            sid = con.execute("INSERT INTO staff (name, sort) VALUES (?,?)", (name, sort)).lastrowid
            db.record(con, "初期取り込み", "staff", sid, "追加", name)
            added.append(name)
    print(f"追加 {len(added)} 人 / 登録済みのため飛ばした {len(skipped)} 人")
    if added:
        print("追加した名前: " + "、".join(added))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "staff.txt")
