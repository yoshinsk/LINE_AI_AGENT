r"""<PROJECT_ROOT>\src\line_ai_agent\office_text.py

LINEで受信したWord、Excel、PowerPoint、PDFから、Codexへ渡す安全なプレーンテキストを抽出します。
"""

from __future__ import annotations

from pathlib import Path
import posixpath
import xml.etree.ElementTree as ElementTree
import zipfile

import fitz

WORDPROCESSING_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PRESENTATION_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
OFFICE_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_TEXT_SUFFIXES = {".docx", ".xlsx", ".pptx", ".pdf"}


class OfficeTextExtractionError(ValueError):
    """破損したOffice Open XMLなど、添付処理だけを継続すべき抽出失敗を表します。"""


def extract_office_text(path: Path, max_chars: int) -> str | None:
    """DOCX、XLSX、PPTX、PDFの本文・セル値を抽出し、プロンプト投入量を上限で制限します。"""
    suffix = path.suffix.lower()
    if suffix not in OFFICE_TEXT_SUFFIXES:
        return None
    try:
        if suffix == ".docx":
            text = _extract_docx_text(path)
        elif suffix == ".xlsx":
            text = _extract_xlsx_text(path)
        elif suffix == ".pptx":
            text = _extract_pptx_text(path)
        else:
            text = _extract_pdf_text(path)
    except (ElementTree.ParseError, KeyError, OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        raise OfficeTextExtractionError(f"{path.name}: {exc}") from exc
    return _clip_extracted_text(text, max_chars)


def _extract_docx_text(path: Path) -> str:
    """Word本文、脚注、文末脚注を段落順に取り出します。"""
    member_names = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")
    sections: list[str] = []
    with zipfile.ZipFile(path) as archive:
        available = set(archive.namelist())
        for member_name in member_names:
            if member_name not in available:
                continue
            root = ElementTree.fromstring(archive.read(member_name))
            paragraphs = [_word_paragraph_text(item) for item in root.findall(f".//{{{WORDPROCESSING_NS}}}p")]
            content = "\n".join(item for item in paragraphs if item)
            if content:
                label = {
                    "word/document.xml": "本文",
                    "word/footnotes.xml": "脚注",
                    "word/endnotes.xml": "文末脚注",
                }[member_name]
                sections.append(f"[{label}]\n{content}")
    if not sections:
        raise ValueError("DOCXから読取可能なテキストを取得できませんでした")
    return "\n\n".join(sections)


def _word_paragraph_text(paragraph: ElementTree.Element) -> str:
    """Word段落内の文字列、タブ、改行を元の順序でプレーンテキスト化します。"""
    parts: list[str] = []
    for item in paragraph.iter():
        if item.tag == f"{{{WORDPROCESSING_NS}}}t":
            parts.append(item.text or "")
        elif item.tag == f"{{{WORDPROCESSING_NS}}}tab":
            parts.append("\t")
        elif item.tag in {f"{{{WORDPROCESSING_NS}}}br", f"{{{WORDPROCESSING_NS}}}cr"}:
            parts.append("\n")
    return "".join(parts).strip()


def _extract_xlsx_text(path: Path) -> str:
    """Excelブックのシート名、セル番地、表示値、数式をシート順に取り出します。"""
    with zipfile.ZipFile(path) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheets = _xlsx_sheets(archive)
        sections: list[str] = []
        for sheet_name, member_name in sheets:
            try:
                root = ElementTree.fromstring(archive.read(member_name))
            except KeyError:
                continue
            rows: list[str] = []
            for row in root.findall(f".//{{{SPREADSHEET_NS}}}sheetData/{{{SPREADSHEET_NS}}}row"):
                cells: list[str] = []
                for cell in row.findall(f"{{{SPREADSHEET_NS}}}c"):
                    value = _xlsx_cell_text(cell, shared_strings)
                    if value == "":
                        continue
                    coordinate = cell.attrib.get("r", "?")
                    cells.append(f"{coordinate}={value}")
                if cells:
                    row_number = row.attrib.get("r", "?")
                    rows.append(f"行 {row_number}: " + " | ".join(cells))
            if rows:
                sections.append(f"[シート: {sheet_name}]\n" + "\n".join(rows))
    if not sections:
        raise ValueError("XLSXから読取可能なセル値を取得できませんでした")
    return "\n\n".join(sections)


def _extract_pptx_text(path: Path) -> str:
    """PowerPointのスライドを表示順に走査し、テキストボックスと表の文字列を抽出します。"""
    with zipfile.ZipFile(path) as archive:
        members = sorted(
            (name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")),
            key=_pptx_slide_sort_key,
        )
        sections: list[str] = []
        for index, member_name in enumerate(members, start=1):
            root = ElementTree.fromstring(archive.read(member_name))
            paragraphs = [
                "".join(item.text or "" for item in paragraph.iter(f"{{{PRESENTATION_DRAWING_NS}}}t")).strip()
                for paragraph in root.findall(f".//{{{PRESENTATION_DRAWING_NS}}}p")
            ]
            content = "\n".join(item for item in paragraphs if item)
            if content:
                sections.append(f"[スライド {index}]\n{content}")
    if not sections:
        raise ValueError("PPTXから読取可能なテキストを取得できませんでした")
    return "\n\n".join(sections)


def _pptx_slide_sort_key(member_name: str) -> tuple[int, str]:
    """slide10.xmlがslide2.xmlより前にならないよう、スライド番号を数値で並べます。"""
    stem = Path(member_name).stem
    suffix = stem.removeprefix("slide")
    try:
        return int(suffix), member_name
    except ValueError:
        return 10**9, member_name


def _extract_pdf_text(path: Path) -> str:
    """PDFの各ページからテキストを抽出し、テキスト層がないページも明示してCodexへ渡します。"""
    document = fitz.open(path)
    try:
        if document.needs_pass:
            raise ValueError("パスワード保護されたPDFは処理できません")
        sections: list[str] = []
        for index, page in enumerate(document, start=1):
            content = page.get_text("text").strip()
            if content:
                sections.append(f"[ページ {index}]\n{content}")
            else:
                sections.append(f"[ページ {index}]\n[テキスト層がないため本文を抽出できません]")
        if not sections:
            raise ValueError("PDFにページがありません")
        return "\n\n".join(sections)
    finally:
        document.close()


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """共有文字列テーブルをインデックス順に読み、リッチテキストも連結します。"""
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: list[str] = []
    for item in root.findall(f"{{{SPREADSHEET_NS}}}si"):
        values.append("".join(text.text or "" for text in item.iter(f"{{{SPREADSHEET_NS}}}t")))
    return values


def _xlsx_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """workbook.xmlと関連定義から、表示順のシート名とXMLパスを解決します。"""
    try:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except KeyError:
        return _xlsx_fallback_sheets(archive)

    targets = {
        item.attrib.get("Id", ""): item.attrib.get("Target", "")
        for item in relationships.findall(f"{{{PACKAGE_RELATIONSHIPS_NS}}}Relationship")
    }
    sheets: list[tuple[str, str]] = []
    for index, item in enumerate(workbook.findall(f".//{{{SPREADSHEET_NS}}}sheet"), start=1):
        relation_id = item.attrib.get(f"{{{OFFICE_RELATIONSHIPS_NS}}}id", "")
        target = targets.get(relation_id, "")
        member_name = _xlsx_member_name(target)
        if member_name in archive.namelist():
            sheets.append((item.attrib.get("name", f"Sheet{index}"), member_name))
    return sheets or _xlsx_fallback_sheets(archive)


def _xlsx_member_name(target: str) -> str:
    """workbook.xml.relsの相対TargetをZip内部のxl配下パスへ正規化します。"""
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join("xl", target))


def _xlsx_fallback_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """関連定義が不完全なブックでもworksheet XMLを名前順で読めるようにします。"""
    members = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/") and name.endswith(".xml"))
    return [(Path(member_name).stem, member_name) for member_name in members]


def _xlsx_cell_text(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    """Excelセルを共有文字列、inline文字列、値、数式の順に解釈します。"""
    cell_type = cell.attrib.get("t", "")
    formula = cell.findtext(f"{{{SPREADSHEET_NS}}}f", default="")
    if cell_type == "inlineStr":
        value = "".join(text.text or "" for text in cell.iter(f"{{{SPREADSHEET_NS}}}t"))
    else:
        value = cell.findtext(f"{{{SPREADSHEET_NS}}}v", default="")
        if cell_type == "s":
            try:
                value = shared_strings[int(value)]
            except (IndexError, ValueError):
                value = ""
        elif cell_type == "b":
            value = "TRUE" if value == "1" else "FALSE"
    if formula:
        formula_text = "=" + formula.lstrip("=")
        return f"{value} [数式: {formula_text}]" if value else f"[数式: {formula_text}]"
    return value.strip()


def _clip_extracted_text(text: str, max_chars: int) -> str:
    """巨大なOffice文書がCodexの入力上限を占有しないよう、明示的な末尾表示で切り詰めます。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    limit = max(1_000, max_chars)
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + f"\n\n[抽出テキストは {limit} 文字で省略しました]"
