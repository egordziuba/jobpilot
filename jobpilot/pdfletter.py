"""Anschreiben als PDF (DIN 5008, Form B) — без сторонних библиотек: Helvetica + WinAnsi, перенос строк по ширине глифов."""
from __future__ import annotations

import zlib
from datetime import date
from pathlib import Path

# Ширины глифов Helvetica / Helvetica-Bold для кодировки Windows-1252 (на 1000 единиц)
W_REG = [761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584, 761, 556, 0, 222, 556, 333, 1000, 556, 556, 333, 1000, 667, 333, 1000, 0, 611, 0, 0, 222, 222, 333, 333, 350, 556, 1000, 333, 1000, 500, 333, 944, 0, 500, 667, 278, 333, 556, 556, 556, 556, 260, 556, 333, 737, 370, 556, 584, 333, 737, 333, 400, 584, 333, 333, 333, 556, 537, 278, 333, 333, 365, 556, 834, 834, 834, 611, 667, 667, 667, 667, 667, 667, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278, 722, 722, 778, 778, 778, 778, 778, 584, 778, 722, 722, 722, 722, 667, 667, 611, 556, 556, 556, 556, 556, 556, 889, 500, 556, 556, 556, 556, 278, 278, 278, 278, 556, 556, 556, 556, 556, 556, 556, 584, 611, 556, 556, 556, 556, 500, 556, 500]
W_BOLD = [761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 761, 278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584, 761, 556, 0, 278, 556, 500, 1000, 556, 556, 333, 1000, 667, 333, 1000, 0, 611, 0, 0, 278, 278, 500, 500, 350, 556, 1000, 333, 1000, 556, 333, 944, 0, 500, 667, 278, 333, 556, 556, 556, 556, 280, 556, 333, 737, 370, 556, 584, 333, 737, 333, 400, 584, 333, 333, 333, 611, 556, 278, 333, 333, 365, 556, 834, 834, 834, 611, 722, 722, 722, 722, 722, 722, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278, 722, 722, 778, 778, 778, 778, 778, 584, 778, 722, 722, 722, 722, 667, 667, 611, 556, 556, 556, 556, 556, 556, 889, 556, 556, 556, 556, 556, 278, 278, 278, 278, 611, 611, 611, 611, 611, 611, 611, 584, 611, 611, 611, 611, 611, 556, 611, 556]

MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
NAVY = "0.090 0.196 0.302"
GREY = "0.33 0.40 0.47"
INK = "0.12 0.16 0.20"

A4_W, A4_H = 595.28, 841.89
MM = 72 / 25.4
LEFT, RIGHT = 25 * MM, 20 * MM
TEXT_W = A4_W - LEFT - RIGHT


def _enc(text: str) -> bytes:
    return text.encode("cp1252", errors="replace")


def _width(text: str, size: float, bold: bool = False) -> float:
    table = W_BOLD if bold else W_REG
    return sum(table[b] for b in _enc(text)) * size / 1000


def _esc(text: str) -> str:
    raw = _enc(text).decode("latin-1")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap(text: str, size: float, width: float, bold: bool = False) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for word in words:
            trial = word if not cur else cur + " " + word
            if _width(trial, size, bold) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def german_date(d: date) -> str:
    return f"{d.day}. {MONTHS_DE[d.month - 1]} {d.year}"


def split_body(body: str) -> tuple[str, str]:
    """Отделяет подпись (адрес/телефон уже в шапке). Возвращает (текст до прощания включительно, имя)."""
    lines = body.rstrip().split("\n")
    for i, line in enumerate(lines):
        if line.strip() in ("Mit freundlichen Grüßen", "Kind regards", "Best regards"):
            name = lines[i + 1].strip() if i + 1 < len(lines) else ""
            return "\n".join(lines[: i + 1]), name
    return body, ""


def build_pdf(path: Path, applicant: dict, recipient: list[str], subject: str, body: str,
              place: str = "", when: date | None = None, lang: str = "de") -> Path:
    when = when or date.today()
    place = place or applicant.get("address", "").split()[-1]
    ops: list[str] = []

    def text(x: float, y: float, s: str, size: float = 10.5, bold: bool = False, color: str = INK) -> None:
        font = "F2" if bold else "F1"
        ops.append(f"BT {color} rg /{font} {size:.2f} Tf {x:.2f} {y:.2f} Td ({_esc(s)}) Tj ET")

    top = A4_H - 18 * MM
    text(LEFT, top, applicant["name"], 17, True, NAVY)
    contact = f'{applicant["address"]}  ·  {applicant["phone"]}  ·  {applicant["email"]}'
    text(LEFT, top - 15, contact, 8.8, False, GREY)
    ops.append(f"{NAVY} RG 0.8 w {LEFT:.2f} {top - 23:.2f} m {A4_W - RIGHT:.2f} {top - 23:.2f} l S")

    # Absenderzeile + Anschrift (Anschriftfeld ab 50,8 mm von oben)
    y = A4_H - 50.8 * MM
    sender_line = f'{applicant["name"]} · {applicant["address"]}'
    text(LEFT, y, sender_line, 7, False, GREY)
    y -= 16
    for line in recipient:
        text(LEFT, y, line, 10.5)
        y -= 14

    date_str = f"{place}, {german_date(when)}" if lang == "de" else f"{place}, {when:%d %B %Y}"
    y_date = A4_H - 98.4 * MM
    text(A4_W - RIGHT - _width(date_str, 10.5), y_date, date_str, 10.5)

    y = y_date - 26
    for line in wrap(subject, 11, TEXT_W, True):
        text(LEFT, y, line, 11, True, NAVY)
        y -= 15
    y -= 10

    main, name = split_body(body)
    size, lead = 10.5, 14.6
    pages: list[list[str]] = []
    for line in wrap(main, size, TEXT_W):
        if y < 25 * MM:
            pages.append(ops)
            ops = []
            y = A4_H - 25 * MM
        if line:
            text(LEFT, y, line, size)
        y -= lead
    if name:
        y -= lead * 1.6
        text(LEFT, y, name, size)
    pages.append(ops)
    return _write(path, pages)


def _write(path: Path, pages: list[list[str]]) -> Path:
    objs: list[bytes] = []

    def add(data: bytes) -> int:
        objs.append(data)
        return len(objs)

    font_r = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    font_b = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    pages_id = len(objs) + 1 + 2 * len(pages)  # зарезервируем номер для /Pages после страниц
    page_ids = []
    for ops in pages:
        stream = zlib.compress("\n".join(ops).encode("latin-1"))
        content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(
            (f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {A4_W:.2f} {A4_H:.2f}] "
             f"/Resources << /Font << /F1 {font_r} 0 R /F2 {font_b} 0 R >> >> /Contents {content} 0 R >>").encode()))
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    assert add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()) == pages_id
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, data in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + data + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path
