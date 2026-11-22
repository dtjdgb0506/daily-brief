"""把 Markdown 转成 Word（支持标题/列表/表格/代码块/粗体/行内代码），用于生成交接文档。

用法: python scripts/md_to_docx.py input.md output.docx
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

BOLD = re.compile(r"\*\*(.+?)\*\*")
CODE = re.compile(r"`([^`]+)`")


def add_inline(paragraph, text: str) -> None:
    """处理 **粗体** 与 `行内代码`。"""
    pos = 0
    pattern = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")
    for m in pattern.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos:m.start()])
        if m.group(1) is not None:
            r = paragraph.add_run(m.group(1))
            r.bold = True
        else:
            r = paragraph.add_run(m.group(2))
            r.font.name = "Consolas"
            r.font.size = Pt(10)
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def parse_table(lines: list[str], i: int) -> tuple[list[list[str]], int]:
    rows = []
    while i < len(lines) and lines[i].strip().startswith("|"):
        row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        if not all(re.fullmatch(r":?-{2,}:?", c) for c in row):
            rows.append(row)
        i += 1
    return rows, i


def convert(md_path: Path, docx_path: Path) -> None:
    lines = md_path.read_text(encoding="utf-8").splitlines()
    doc = Document()

    # 基础字体
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia", "微软雅黑"
    )

    i = 0
    in_code = False
    code_buf: list[str] = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                p = doc.add_paragraph()
                r = p.add_run("\n".join(code_buf))
                r.font.name = "Consolas"
                r.font.size = Pt(9)
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        if stripped.startswith("|"):
            rows, i = parse_table(lines, i)
            if rows:
                cols = max(len(r) for r in rows)
                table = doc.add_table(rows=len(rows), cols=cols)
                table.style = "Light Grid Accent 1"
                for ri, row in enumerate(rows):
                    for ci in range(cols):
                        cell = table.cell(ri, ci)
                        cell.text = ""
                        p = cell.paragraphs[0]
                        add_inline(p, row[ci] if ci < len(row) else "")
                        for run in p.runs:
                            run.font.size = Pt(9)
            continue

        if stripped.startswith("# "):
            p = doc.add_heading("", level=0)
            add_inline(p, stripped[2:])
        elif stripped.startswith("## "):
            p = doc.add_heading("", level=1)
            add_inline(p, stripped[3:])
        elif stripped.startswith("### "):
            p = doc.add_heading("", level=2)
            add_inline(p, stripped[4:])
        elif stripped.startswith("> "):
            p = doc.add_paragraph()
            r = p.add_run(stripped[2:])
            r.italic = True
            r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
        elif re.match(r"^\d+\.\s", stripped):
            p = doc.add_paragraph(style="List Number")
            add_inline(p, re.sub(r"^\d+\.\s", "", stripped))
        elif stripped.startswith("- "):
            p = doc.add_paragraph(style="List Bullet")
            add_inline(p, stripped[2:])
        elif stripped == "---":
            doc.add_paragraph("")
        else:
            p = doc.add_paragraph()
            add_inline(p, stripped)
        i += 1

    if code_buf:
        p = doc.add_paragraph()
        r = p.add_run("\n".join(code_buf))
        r.font.name = "Consolas"

    doc.save(docx_path)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python scripts/md_to_docx.py input.md output.docx")
        sys.exit(2)
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
    print("written:", sys.argv[2])
