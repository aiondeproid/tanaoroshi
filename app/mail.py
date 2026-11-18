"""分類の画面と期限警告一覧の「メールで送る」ボタンから、選んだ送り先にメールを送る（自動では送らない）。

送り先は確認の役割（担当者・前工程L・生産管理・品管）ごとに管理画面で登録する（notify_to テーブル）。
送り方は環境変数 TANAOROSHI_MAIL_VIA で選ぶ。
  notes   … このPCの Notes（ログインしている人のメールファイル）から送る。メールサーバーの設定は要らない
  outlook … このPCの Outlook（サインインしている人のアドレス）から送る。メールサーバーの設定は要らない
  smtp    … TANAOROSHI_SMTP_HOST のメールサーバーに送る（MAIL_VIA が空でも SMTP_HOST があればこれ）
どちらでもなければメールは送らない。
"""
import os
import smtplib
import threading
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import db


def config() -> dict:
    via = os.environ.get("TANAOROSHI_MAIL_VIA", "").strip().lower()
    if via not in ("notes", "outlook", "smtp"):
        via = "smtp" if os.environ.get("TANAOROSHI_SMTP_HOST", "").strip() else ""
    return {
        "via": via,
        "host": os.environ.get("TANAOROSHI_SMTP_HOST", "").strip(),
        "port": int(os.environ.get("TANAOROSHI_SMTP_PORT", "25") or 25),
        "user": os.environ.get("TANAOROSHI_SMTP_USER", "").strip(),
        "password": os.environ.get("TANAOROSHI_SMTP_PASSWORD", ""),
        "starttls": os.environ.get("TANAOROSHI_SMTP_STARTTLS", "").strip().lower() in ("1", "true", "yes", "on"),
        "sender": os.environ.get("TANAOROSHI_MAIL_FROM", "").strip(),
        "app_url": os.environ.get("TANAOROSHI_URL", "").strip().rstrip("/"),
    }


def enabled() -> bool:
    c = config()
    if c["via"] in ("notes", "outlook"):
        return True
    return bool(c["via"] == "smtp" and c["host"] and c["sender"])


def recipients(con, role: str) -> list[str]:
    return [r["email"] for r in con.execute("SELECT email FROM notify_to WHERE role=? ORDER BY id", (role,))]


# Notes・Outlook は同時に操作しないよう1通ずつ送る
_com_lock = threading.Lock()


def _com_acquire(app: str) -> None:
    # 前の1通がパスワードの画面などで止まっていると返ってこない。待ち続けずに失敗として履歴に残す
    if not _com_lock.acquire(timeout=60):
        raise TimeoutError(f"{app} が応答しません。{app} を開いて、画面が止まっていないか確かめてください")


def send_notes(to: list[str], subject: str, body: str) -> None:
    """このPCの Notes から送る。ログインしている人のメールファイルを使い、送ったメールは「送信済み」に残る。
    Notes は32ビットなので、別のプロセスで動く OLE（Notes.NotesSession）を使う（64ビットの Python からも呼べる）。
    Notes を起動してログインしておくこと。閉じているとパスワードを聞かれて止まる。"""
    import pythoncom
    import win32com.client

    _com_acquire("Notes")
    try:
        pythoncom.CoInitialize()
        try:
            # 引数なしのメソッドがプロパティと見なされないよう、メソッドとして呼ぶものを指定する
            ses = win32com.client.Dispatch("Notes.NotesSession")
            ses._FlagAsMethod("GetDatabase")
            mdb = ses.GetDatabase("", "")
            mdb._FlagAsMethod("OpenMail", "CreateDocument")
            mdb.OpenMail()
            if not mdb.IsOpen:
                raise RuntimeError("Notes のメールファイルを開けません")
            doc = mdb.CreateDocument()
            doc._FlagAsMethod("ReplaceItemValue", "CreateRichTextItem", "Send")
            doc.ReplaceItemValue("Form", "Memo")
            doc.ReplaceItemValue("SendTo", list(to))
            doc.ReplaceItemValue("Subject", subject)
            rt = doc.CreateRichTextItem("Body")
            rt._FlagAsMethod("AppendText", "AddNewLine")
            for i, line in enumerate(body.split("\n")):
                if i:
                    rt.AddNewLine(1)
                rt.AppendText(line)
            doc.SaveMessageOnSend = True
            doc.Send(False)
        finally:
            pythoncom.CoUninitialize()
    finally:
        _com_lock.release()


def send_outlook(to: list[str], subject: str, body: str) -> None:
    """このPCの Outlook（既定のアカウント）から送る。送ったメールは Outlook の「送信済みアイテム」に残る。
    send_with_timeout が別のスレッドで呼ぶので、スレッドごとに COM を初期化する。同時に操作しないよう1通ずつ送る。"""
    import pythoncom
    import win32com.client

    _com_acquire("Outlook")
    try:
        pythoncom.CoInitialize()
        try:
            ol = win32com.client.Dispatch("Outlook.Application")
            m = ol.CreateItem(0)  # 0 = olMailItem
            m.To = "; ".join(to)
            m.Subject = subject
            m.Body = body
            m.Send()
        finally:
            pythoncom.CoUninitialize()
    finally:
        _com_lock.release()


def send(to: list[str], subject: str, body: str) -> None:
    """1通送る。失敗したら例外を投げる。"""
    c = config()
    if c["via"] == "notes":
        return send_notes(to, subject, body)
    if c["via"] == "outlook":
        return send_outlook(to, subject, body)
    msg = EmailMessage()
    msg["From"] = c["sender"]
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    with smtplib.SMTP(c["host"], c["port"], timeout=15) as s:
        if c["starttls"]:
            s.starttls()
        if c["user"]:
            s.login(c["user"], c["password"])
        s.send_message(msg)


def app_url() -> str:
    """メールに書く画面のアドレス。管理画面で入れたもの（settings の mail_url）を優先し、
    なければ環境変数 TANAOROSHI_URL（start.bat が http://<PC名>:8000 を入れる）を使う。"""
    with db.connect() as con:
        r = con.execute("SELECT value FROM settings WHERE key='mail_url'").fetchone()
    return (r["value"] if r and r["value"] else config()["app_url"]).rstrip("/")


def link(path: str = "") -> str:
    url = app_url()
    return f"\n\n開く: {url}/{path}" if url else ""


def send_with_timeout(to: list[str], subject: str, body: str, timeout: float = 90) -> None:
    """画面のボタンから送るとき用。Notes がパスワードの画面などで止まっていても、画面を待たせ続けない。
    時間内に終わらなければ TimeoutError にする（止まった送信は裏に残り、Notes が戻れば送られることがある）。"""
    err: list[BaseException] = []

    def run():
        try:
            send(to, subject, body)
        except BaseException as e:  # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError("メールソフトが応答しません。Notes が起動してログインしているか、画面が止まっていないか確かめてください")
    if err:
        raise err[0]
