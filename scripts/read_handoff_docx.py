"""读取 docx 的文本、表格与结构，用于交接文档审阅。"""

import sys
from docx import Document


def dump(path: str) -> str:
    doc = Document(path)
    out: list[str] = []

    out.append(f"=== 段落/标题（共 {len(doc.paragraphs)} 段）===")
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style = p.style.name if p.style else ""
        prefix = f"[{style}] " if style and style != "Normal" else ""
        out.append(prefix + text)

    out.append(f"\n=== 表格（共 {len(doc.tables)} 个）===")
    for ti, table in enumerate(doc.tables, 1):
        out.append(f"\n--- 表 {ti}（{len(table.rows)} 行 x {len(table.columns)} 列）---")
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " / ") for c in row.cells]
            out.append(" | ".join(cells))

    return "\n".join(out)


if __name__ == "__main__":
    print(dump(sys.argv[1]))
