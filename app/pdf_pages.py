"""資料のPDFをページごとの画像（PNG）にする。タブレットのブラウザではPDFを埋め込めないことがあるため。
pypdfium2 が入っていなければ available() が False になり、画面は画像なしで動く。"""
import struct
import zlib
from pathlib import Path

try:
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover
    pdfium = None

# 画像の幅（px）。thumb=一覧のサムネイル、large=タップして大きく見るとき
WIDTHS = {"thumb": 240, "large": 1200}


def available() -> bool:
    return pdfium is not None


def page_count(pdf: Path) -> int:
    doc = pdfium.PdfDocument(str(pdf))
    try:
        return len(doc)
    finally:
        doc.close()


def png(width: int, height: int, channels: int, stride: int, buf: bytes) -> bytes:
    """画素の並び（RGB/RGBA）をPNGにする。Pillow を入れずに済ませるため自前で書く。"""
    row = width * channels
    raw = b"".join(b"\0" + buf[y * stride:y * stride + row] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    color = {3: 2, 4: 6}[channels]
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def render(pdf: Path, page_no: int, width: int) -> bytes:
    """page_no は 1 から。"""
    doc = pdfium.PdfDocument(str(pdf))
    try:
        page = doc[page_no - 1]
        bmp = page.render(scale=width / page.get_width(), rev_byteorder=True)  # BGR → RGB
        return png(bmp.width, bmp.height, bmp.n_channels, bmp.stride, bytes(bmp.buffer))
    finally:
        doc.close()
