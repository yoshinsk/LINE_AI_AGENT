r"""<PROJECT_ROOT>\tests\test_office_text.py

DOCX/XLSX本文抽出と、ワーカーがCodex用テキストを保存する経路を検証します。
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

import fitz

from line_ai_agent.office_text import extract_office_text
from line_ai_agent.worker import LineWorker


WORD_DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>第一段落です。</w:t></w:r></w:p>
    <w:p><w:r><w:t>志望動機</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>を記載します。</w:t></w:r></w:p>
  </w:body>
</w:document>
"""

WORKBOOK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="志望校" sheetId="1" r:id="rId1"/></sheets>
</workbook>
"""

WORKBOOK_RELS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""

SHARED_STRINGS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2">
  <si><t>第一希望</t></si>
  <si><t>交換留学</t></si>
</sst>
"""

SHEET_XML = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>京都大学</t></is></c></row>
    <row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><f>SUM(1,2)</f><v>3</v></c></row>
  </sheetData>
</worksheet>
"""

PPTX_SLIDE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>発表タイトル</a:t></a:r></a:p><a:p><a:r><a:t>要点です。</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>
"""


class _CopyingAttachmentClient:
    """ダウンロードendpointの代わりにテスト用DOCXをコピーする最小クライアントです。"""

    def __init__(self, fixture: Path) -> None:
        self._fixture = fixture

    def download_attachment(self, attachment_id: int, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._fixture, destination)
        return destination


class OfficeTextTest(unittest.TestCase):
    """Office Open XML添付をCodex向けテキストへ変換する境界を確認します。"""

    def test_extracts_docx_paragraphs_and_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "motivation.docx"
            _write_docx(path)

            text = extract_office_text(path, 10_000)

            self.assertIsNotNone(text)
            self.assertIn("第一段落です。", text)
            self.assertIn("志望動機\tを記載します。", text)

    def test_extracts_xlsx_sheet_values_and_formula(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "schools.xlsx"
            _write_xlsx(path)

            text = extract_office_text(path, 10_000)

            self.assertIsNotNone(text)
            self.assertIn("[シート: 志望校]", text)
            self.assertIn("A1=第一希望", text)
            self.assertIn("B1=京都大学", text)
            self.assertIn("B2=3 [数式: =SUM(1,2)]", text)

    def test_clips_long_office_text_with_notice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "long.docx"
            _write_docx(path, WORD_DOCUMENT_XML.replace("第一段落です。", "あ" * 1_200))

            text = extract_office_text(path, 1_000)

            self.assertIsNotNone(text)
            self.assertIn("抽出テキストは 1000 文字で省略しました", text)

    def test_extracts_pptx_slide_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "presentation.pptx"
            _write_pptx(path)

            text = extract_office_text(path, 10_000)

            self.assertIsNotNone(text)
            self.assertIn("[スライド 1]", text)
            self.assertIn("発表タイトル", text)
            self.assertIn("要点です。", text)

    def test_extracts_pdf_page_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "report.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), "PDF report text")
            document.save(path)
            document.close()

            text = extract_office_text(path, 10_000)

            self.assertIsNotNone(text)
            self.assertIn("[ページ 1]", text)
            self.assertIn("PDF report text", text)

    def test_worker_saves_office_text_snapshot_for_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = Path(temp_dir) / "source.docx"
            _write_docx(fixture)
            download_dir = Path(temp_dir) / "downloads"
            settings = SimpleNamespace(attachment_download_dir=download_dir, attachment_text_max_chars=10_000)
            worker = LineWorker(settings, _CopyingAttachmentClient(fixture), None, None)

            paths = worker._download_attachments(
                42,
                [{"id": 7, "file_name": "motivation.docx", "storage_status": "stored"}],
            )

            self.assertEqual(2, len(paths))
            self.assertEqual(".docx", paths[0].suffix)
            self.assertTrue(paths[1].name.endswith(".line-office-extracted.txt"))
            self.assertIn("志望動機", paths[1].read_text(encoding="utf-8"))

def _write_docx(path: Path, document_xml: str = WORD_DOCUMENT_XML) -> None:
    """最小のDOCXコンテナを作り、抽出処理を実ファイル同様にテストします。"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)


def _write_xlsx(path: Path) -> None:
    """共有文字列、inline文字列、数式を含む最小のXLSXコンテナを作ります。"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", WORKBOOK_XML)
        archive.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS_XML)
        archive.writestr("xl/sharedStrings.xml", SHARED_STRINGS_XML)
        archive.writestr("xl/worksheets/sheet1.xml", SHEET_XML)


def _write_pptx(path: Path) -> None:
    """最小のスライドXMLを含むPPTXコンテナを作り、PowerPoint本文抽出をテストします。"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", PPTX_SLIDE_XML)


if __name__ == "__main__":
    unittest.main()
