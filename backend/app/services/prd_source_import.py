"""Extract reviewable text from a PRD supplied during project intake."""

from __future__ import annotations

import base64
import binascii
import io
from pathlib import Path

from docx import Document as WordDocument
from docx.table import Table
from docx.text.paragraph import Paragraph


MAX_SOURCE_BYTES = 24 * 1024 * 1024
MAX_SOURCE_TEXT = 100_000
SUPPORTED_EXTENSIONS = {".docx", ".md", ".pdf", ".txt"}


def import_prd_source(filename: str, content_base64: str) -> dict[str, str]:
    """Decode and extract a bounded, text-only baseline for downstream Agents."""

    safe_name = Path(filename).name.strip()
    extension = Path(safe_name).suffix.lower()
    if not safe_name or extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("支持 DOCX、Markdown、TXT 或文字型 PDF 文件")
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("PRD 文件编码无效") from error
    if not raw:
        raise ValueError("PRD 文件不能为空")
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("PRD 文件不能超过 24 MB")

    if extension in {".md", ".txt"}:
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("文本 PRD 必须使用 UTF-8 编码") from error
    elif extension == ".pdf":
        content = _pdf_text(raw)
    else:
        content = _docx_text(raw)

    content = content.strip()
    if not content:
        raise ValueError("PRD 中没有可提取的文字")
    if len(content) > MAX_SOURCE_TEXT:
        raise ValueError("PRD 提取文字不能超过 100000 个字符")
    return {
        "filename": safe_name,
        "media_type": _media_type(extension),
        "title": _title(content, safe_name),
        "content": content,
    }


def _pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
    except ModuleNotFoundError as error:
        raise ValueError("当前服务未安装 PDF 文字解析组件，请改用 DOCX、Markdown 或 TXT") from error
    except Exception as error:
        raise ValueError("PDF 文件无效或已损坏") from error
    if reader.is_encrypted:
        raise ValueError("不支持加密 PDF")
    if len(reader.pages) > 50:
        raise ValueError("PDF 不能超过 50 页")
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _docx_text(raw: bytes) -> str:
    try:
        document = WordDocument(io.BytesIO(raw))
    except Exception as error:
        raise ValueError("DOCX 文件无效或已损坏") from error

    parts: list[str] = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = paragraph.style.name if paragraph.style else ""
            if style.startswith("Heading"):
                try:
                    level = min(6, max(1, int(style.split()[-1])))
                except ValueError:
                    level = 2
                parts.append(f"{'#' * level} {text}")
            elif style.startswith("List Bullet"):
                parts.append(f"- {text}")
            elif style.startswith("List Number"):
                parts.append(f"1. {text}")
            else:
                parts.append(text)
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            rows = [[cell.text.replace("|", "\\|").strip() for cell in row.cells] for row in table.rows]
            if not rows:
                continue
            width = max(len(row) for row in rows)
            rows = [row + [""] * (width - len(row)) for row in rows]
            parts.extend(
                [
                    "| " + " | ".join(rows[0]) + " |",
                    "| " + " | ".join("---" for _ in range(width)) + " |",
                    *("| " + " | ".join(row) + " |" for row in rows[1:]),
                ]
            )
    return "\n\n".join(parts)


def _title(content: str, filename: str) -> str:
    for line in content.splitlines():
        candidate = line.strip().lstrip("#").strip()
        if candidate:
            return candidate[:100]
    return Path(filename).stem[:100] or "导入的 PRD"


def _media_type(extension: str) -> str:
    return {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".md": "text/markdown",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
    }[extension]
