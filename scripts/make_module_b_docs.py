"""Документы модуля B из Markdown в DOCX и PDF (LibreOffice): ТЗ, программа и методика, отчёт.

    python scripts/make_module_b_docs.py          # module_b/*.md → module_b/*.docx и *.pdf

Нужен LibreOffice (soffice в PATH или в стандартном каталоге). После заполнения отчёта на площадке — запустить снова.
"""
from __future__ import annotations

import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["tbo-tracker-tz", "tbo-tracker-pmi", "tbo-tracker-module-b-report"]
CSS = """body{font-family:'Times New Roman';font-size:12pt}h1{font-size:15pt;text-align:center}h2{font-size:13.5pt}
h3{font-size:12pt}table{border-collapse:collapse;width:100%;margin:4pt 0}td,th{border:1px solid #000;padding:2pt 4pt;
font-size:10pt;vertical-align:top}th{background:#eeeeee}code{font-family:'Courier New';font-size:9.5pt}"""


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", s)


def md_to_html(md: str, title: str) -> str:
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            headless = bool(rows) and not any(rows[0])
            if headless:
                rows = rows[1:]                       # таблица «поле — значение» без шапки
            out.append('<table border="1" cellspacing="0" cellpadding="3" width="100%">')
            for k, r in enumerate(rows):
                tag = "th" if k == 0 and not headless else "td"
                out.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in r) + "</tr>")
            out.append("</table>")
            continue
        m = re.match(r"(#{1,3}) (.*)", ln)
        if m:
            out.append(f"<h{len(m.group(1))}>{inline(m.group(2))}</h{len(m.group(1))}>")
        elif re.match(r"\s*- ", ln):
            items = []
            while i < len(lines) and (re.match(r"\s*- ", lines[i]) or (lines[i].startswith("  ") and items)):
                if re.match(r"\s*- ", lines[i]):
                    items.append(re.sub(r"^\s*- ", "", lines[i]))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue
        elif ln.strip():
            para = [ln]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"(#|\||\s*- )", lines[i + 1]):
                i += 1
                para.append(lines[i])
            out.append(f"<p>{inline(' '.join(p.strip() for p in para))}</p>")
        i += 1
    return f"<html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{CSS}</style></head><body>{''.join(out)}</body></html>"


def soffice() -> str:
    for c in (shutil.which("soffice"), r"C:\Program Files\LibreOffice\program\soffice.exe", "/usr/bin/soffice",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if c and Path(c).exists():
            return c
    sys.exit("LibreOffice не найден")


def main() -> int:
    so, folder = soffice(), ROOT / "module_b"
    tmp = Path(tempfile.mkdtemp(prefix="tbo-docs-"))
    for name in DOCS:
        md = (folder / f"{name}.md").read_text("utf-8")
        title = md.splitlines()[0].lstrip("# ")
        src = tmp / f"{name}.html"
        src.write_text(md_to_html(md, title), "utf-8")
        subprocess.run([so, "--headless", "--convert-to", "docx:MS Word 2007 XML", "--outdir", str(folder), str(src)],
                       check=True, capture_output=True)
        subprocess.run([so, "--headless", "--convert-to", "pdf", "--outdir", str(folder), str(folder / f"{name}.docx")],
                       check=True, capture_output=True)
        print(folder / f"{name}.docx", folder / f"{name}.pdf")
    return 0


if __name__ == "__main__":
    sys.exit(main())
