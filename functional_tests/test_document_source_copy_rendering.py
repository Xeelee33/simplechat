# test_document_source_copy_rendering.py
"""Functional tests for isolated Markdown source-copy rendering.

Version: 0.261.053
Implemented in: 0.261.053

The tests inspect real DOCX and PDF documents, including their links,
structure, pagination, rejection behavior, and network isolation.
"""

import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import fitz
from docx import Document
from lxml import etree


APP_DIR = Path(__file__).resolve().parents[1] / "application" / "single_app"
sys.path.insert(0, str(APP_DIR))

from functions_document_source_copy import (
    MAX_SOURCE_BYTES,
    SourceCopyError,
    render_document_source_copy,
)


render_source_copy = render_document_source_copy


SOURCE = """# Main heading

A café 中文 paragraph with **bold**, *emphasis*, escaped \\*asterisks\\*, and
[a reference](https://example.com/reference?q=1&x=2).

## Details

- First item with **emphasis** and a [link](https://example.com/list).
    - Nested item
- Last item

5. Fifth
6. Sixth

| Name | Value |
| :--- | ---: |
| Alpha | 42 |
| Escaped \\| pipe | café |
"""


class SourceCopyRenderingTests(unittest.TestCase):
    def test_docx_structure_and_full_content(self):
        data = render_source_copy(SOURCE, "docx")
        document = Document(io.BytesIO(data))
        paragraphs = document.paragraphs
        self.assertEqual(paragraphs[0].style.name, "Heading 1")
        self.assertEqual(paragraphs[2].style.name, "Heading 2")
        self.assertIn("café 中文", paragraphs[1].text)
        self.assertIn("*asterisks*", paragraphs[1].text)
        self.assertIn("a reference", paragraphs[1].text)
        self.assertEqual(
            [p.text for p in paragraphs[3:]],
            ["First item with emphasis and a link.", "Nested item", "Last item", "Fifth", "Sixth"],
        )
        self.assertTrue(all(p._p.pPr.numPr is not None for p in paragraphs[3:]))
        self.assertEqual(
            [[cell.text for cell in row.cells] for row in document.tables[0].rows],
            [["Name", "Value"], ["Alpha", "42"], ["Escaped | pipe", "café"]],
        )
        with ZipFile(io.BytesIO(data)) as archive:
            numbering = etree.fromstring(archive.read("word/numbering.xml"))
            namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            self.assertTrue(numbering.xpath("//w:start[@w:val='5']", namespaces=namespaces))
            tags = [etree.QName(child).localname for child in numbering]
            self.assertLess(max(i for i, tag in enumerate(tags) if tag == "abstractNum"),
                            min(i for i, tag in enumerate(tags) if tag == "num"))
            xml = archive.read("word/document.xml").decode("utf-8")
            relations = archive.read("word/_rels/document.xml.rels").decode("utf-8")
            self.assertIn("<w:hyperlink", xml)
            self.assertIn("https://example.com/reference?q=1&amp;x=2", relations)
            self.assertIn("https://example.com/list", relations)

    def test_pdf_text_tables_numbering_and_links(self):
        data = render_source_copy(SOURCE, "PDF")
        with fitz.open(stream=data, filetype="pdf") as document:
            text = "\n".join(page.get_text() for page in document)
            for expected in ("Main heading", "café 中文", "*asterisks*", "Nested item",
                             "5. Fifth", "6. Sixth", "Name", "Value", "Escaped | pipe"):
                self.assertIn(expected, text)
            links = [link["uri"] for page in document for link in page.get_links()
                     if link.get("uri")]
            self.assertIn("https://example.com/reference?q=1&x=2", links)
            self.assertIn("https://example.com/list", links)
            self.assertEqual(document.get_toc(), [[1, "Main heading", 1], [2, "Details", 1]])

    def test_pdf_extracts_original_ligature_sequences_exactly(self):
        source = "Keep the first item."
        with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
            self.assertEqual(pdf[0].get_text(), "Keep the first item.\n")

        source = (
            "# First office\n\n"
            "fi fl ff ffi ffl ft fj st ct ij IJ\n\n"
            "**first office** *affinity fluff* [first file](https://example.com/first)\n\n"
            "- first item\n"
            "  - office child\n\n"
            "| first | office |\n| --- | --- |\n| affinity | fluff |\n"
        )
        with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
            text = "".join(page.get_text() for page in pdf)
            for expected in ("First office", "fi fl ff ffi ffl ft fj st ct ij IJ",
                             "first office", "affinity fluff", "first file", "first item",
                             "office child", "affinity", "fluff"):
                self.assertIn(expected, text)
            for ligature in ("\ufb00", "\ufb01", "\ufb02", "\ufb03", "\ufb04"):
                self.assertNotIn(ligature, text)
            self.assertEqual(pdf.get_toc()[0][1], "First office")
            self.assertIn("https://example.com/first", [
                link["uri"] for page in pdf for link in page.get_links() if link.get("uri")
            ])

    def test_pdf_preserves_literal_unicode_and_rejects_normalized_substitution(self):
        for source in ("literal ﬁ and ﬂ characters", "café cafe\u0301"):
            with self.subTest(source=source):
                with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
                    self.assertEqual(pdf[0].get_text(), source + "\n")
        for source, rendered in (("first", "<p>ﬁrst</p>"), ("café", "<p>cafe\u0301</p>")):
            with self.subTest(source=source):
                with patch("functions_document_source_copy._pdf_html", return_value=rendered):
                    with self.assertRaisesRegex(SourceCopyError, "preserve all source text"):
                        render_source_copy(source, "pdf")

    def test_pdf_ligature_boundary_work_is_bounded(self):
        source = " ".join(["first"] * 13)
        with patch("functions_document_source_copy.MAX_ELEMENTS", 3):
            document = Document(io.BytesIO(render_source_copy(source, "docx")))
            self.assertEqual(document.paragraphs[0].text, source)
            with self.assertRaisesRegex(SourceCopyError, "too many shaping boundaries"):
                render_source_copy(source, "pdf")

    def test_pdf_pagination_preserves_start_middle_and_end(self):
        source = "\n\n".join(f"Section {i:03d} " + "word " * 120 for i in range(90))
        data = render_source_copy(source, "pdf")
        with fitz.open(stream=data, filetype="pdf") as document:
            self.assertGreater(len(document), 1)
            text = "\n".join(page.get_text() for page in document)
            for marker in ("Section 000", "Section 045", "Section 089"):
                self.assertIn(marker, text)

    def test_multi_page_table_preserves_all_rows(self):
        source = "| Name | Value |\n| --- | --- |\n" + "\n".join(
            f"| row{i:03d} | value{i:03d} |" for i in range(100)
        )
        docx = Document(io.BytesIO(render_source_copy(source, "docx")))
        self.assertEqual(len(docx.tables[0].rows), 101)
        self.assertEqual(docx.tables[0].rows[-1].cells[1].text, "value099")
        with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
            self.assertGreater(len(pdf), 1)
            text = "\n".join(page.get_text() for page in pdf)
            for value in ("row000", "value050", "row099", "value099"):
                self.assertIn(value, text)

    def test_nested_list_items_and_pagination_are_preserved(self):
        source = "\n".join(
            f"- Parent {i:03d} **bold**\n"
            f"    1. Child {i:03d} one\n"
            f"    2. Child {i:03d} two"
            for i in range(75)
        )
        document = Document(io.BytesIO(render_source_copy(source, "docx")))
        self.assertEqual(len(document.paragraphs), 225)
        for i in range(75):
            self.assertEqual(document.paragraphs[i * 3].text, f"Parent {i:03d} bold")
            self.assertEqual(document.paragraphs[i * 3 + 1].text, f"Child {i:03d} one")
            self.assertEqual(document.paragraphs[i * 3 + 2].text, f"Child {i:03d} two")
        with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
            self.assertGreater(len(pdf), 1)
            text = "\n".join(page.get_text() for page in pdf)
            for i in range(75):
                self.assertIn(f"Parent {i:03d}", text)
                self.assertIn(f"Child {i:03d} one", text)
                self.assertIn(f"Child {i:03d} two", text)

    def test_two_space_lists_preserve_structure_text_and_links(self):
        source = (
            "- Parent **start**\n"
            "  3. Child & café\n"
            "    - Grandchild [link](https://example.com/ref)\n"
            "  4. Sibling\n"
            "- Final\n"
        )
        document = Document(io.BytesIO(render_source_copy(source, "docx")))
        self.assertEqual(
            [paragraph.text for paragraph in document.paragraphs],
            ["Parent start", "Child & café", "Grandchild link", "Sibling", "Final"],
        )
        numbering = document.part.numbering_part.element
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        levels = []
        formats = []
        for paragraph in document.paragraphs:
            num_id = paragraph._p.pPr.numPr.numId.val
            number = numbering.xpath(f"./w:num[@w:numId='{num_id}']")[0]
            abstract_id = number.find("{%s}abstractNumId" % ns["w"]).get("{%s}val" % ns["w"])
            abstract = numbering.xpath(f"./w:abstractNum[@w:abstractNumId='{abstract_id}']")[0]
            level = abstract.find("{%s}lvl" % ns["w"])
            levels.append(level.find("{%s}pPr/{%s}ind" % (ns["w"], ns["w"])).get("{%s}left" % ns["w"]))
            formats.append(level.find("{%s}numFmt" % ns["w"]).get("{%s}val" % ns["w"]))
        self.assertEqual(levels, ["720", "1440", "2160", "1440", "720"])
        self.assertEqual(formats, ["bullet", "decimal", "bullet", "decimal", "bullet"])

        with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
            text = pdf[0].get_text()
            for value in ("Parent start", "3. Child & café", "Grandchild link", "4. Sibling", "Final"):
                self.assertIn(value, text)
            words = {word[4]: word[0] for word in pdf[0].get_text("words")}
            self.assertLess(words["Parent"], words["Child"])
            self.assertLess(words["Child"], words["Grandchild"])
            self.assertAlmostEqual(words["Child"], words["Sibling"], delta=10)
            self.assertEqual(pdf[0].get_links()[0]["uri"], "https://example.com/ref")

    def test_two_space_list_continuation_and_four_space_list(self):
        source = (
            "- Parent\n"
            "  - Child\n"
            "    continuation line\n"
            "  - Other\n"
            "- End\n\n"
            "- Four\n"
            "    - Still nested\n"
        )
        expected = ["Parent", "Child continuation line", "Other", "End", "Four", "Still nested"]
        document = Document(io.BytesIO(render_source_copy(source, "docx")))
        self.assertEqual([paragraph.text for paragraph in document.paragraphs], expected)
        with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
            text = pdf[0].get_text()
            for item in expected:
                self.assertIn(item, text)

    def test_two_space_lists_with_blank_lines_and_depth_limit(self):
        source = "- Parent\n\n  - Nested after blank\n  - Sibling\n"
        document = Document(io.BytesIO(render_source_copy(source, "docx")))
        self.assertEqual([p.text for p in document.paragraphs],
                         ["Parent", "Nested after blank", "Sibling"])
        with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
            text = pdf[0].get_text()
            self.assertIn("Parent", text)
            self.assertIn("Nested after blank", text)
            self.assertIn("Sibling", text)
        maximum = "\n".join(f"{'  ' * i}- depth{i}" for i in range(8))
        document = Document(io.BytesIO(render_source_copy(maximum, "docx")))
        self.assertEqual([p.text for p in document.paragraphs],
                         [f"depth{i}" for i in range(8)])
        with fitz.open(stream=render_source_copy(maximum, "pdf"), filetype="pdf") as pdf:
            text = pdf[0].get_text()
            for i in range(8):
                self.assertIn(f"depth{i}", text)
        for fmt in ("docx", "pdf"):
            with self.assertRaisesRegex(SourceCopyError, "nesting"):
                render_source_copy(maximum + "\n                - depth8", fmt)

    def test_escaped_text_references_and_soft_breaks(self):
        source = (
            "Escaped \\<tag\\> \\| \\~ \\*star\\*\ncontinued "
            "[citation][source].\n\n[source]: https://example.com/source"
        )
        document = Document(io.BytesIO(render_source_copy(source, "docx")))
        self.assertEqual(
            document.paragraphs[0].text,
            "Escaped <tag> | ~ *star* continued citation.",
        )
        with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
            self.assertIn("Escaped <tag> | ~ *star* continued citation.", pdf[0].get_text())
            self.assertEqual(pdf[0].get_links()[0]["uri"], "https://example.com/source")

    def test_hard_breaks_and_indented_code_whitespace(self):
        source = "First  \nSecond\n\n    alpha  beta\n        indented\n    last"
        document = Document(io.BytesIO(render_source_copy(source, "docx")))
        self.assertEqual(document.paragraphs[0].text, "First\nSecond")
        self.assertEqual(document.paragraphs[1].text, "alpha  beta\n    indented\nlast\n")
        with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
            self.assertIn("First\nSecond\n", pdf[0].get_text())
            self.assertIn("alpha  beta\n    indented\nlast\n", pdf[0].get_text())

    def test_fenced_code_preserves_literal_cli_examples(self):
        body = (
            "curl 'https://example.com/a?x=1&y=2'\n"
            "    echo \"<script> & ![image](https://example.com/x)\"\n"
            "\n"
            "1) ~~literal~~ [missing][reference]\n"
        )
        source = f"Before\n\n```bash\n{body}```\n\nAfter"
        with patch("socket.socket.connect", side_effect=AssertionError("network access")), \
                patch("socket.create_connection", side_effect=AssertionError("network access")):
            docx = Document(io.BytesIO(render_source_copy(source, "docx")))
            self.assertEqual([p.text for p in docx.paragraphs], ["Before", body, "After"])
            self.assertEqual(docx.paragraphs[1].runs[0].font.name, "Consolas")
            with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
                text = "".join(page.get_text() for page in pdf)
                self.assertIn(body.replace("\n\n", "\n \n"), text)
                self.assertNotIn("```", text)
                self.assertEqual([link for page in pdf for link in page.get_links()], [])

    def test_fence_markers_labels_and_indentation(self):
        source = (
            "  ~~~~powershell\n"
            "  Get-Item <path>\n"
            "    Write-Output 'café'\n"
            "  ```nested literal\n"
            "  ~~~~~\n"
            "\n"
            "````text\n"
            "```\n"
            "````\n"
        )
        expected = ["Get-Item <path>\n  Write-Output 'café'\n```nested literal\n", "```\n"]
        document = Document(io.BytesIO(render_source_copy(source, "docx")))
        self.assertEqual([p.text for p in document.paragraphs], expected)
        with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
            text = "".join(page.get_text() for page in pdf)
            for block in expected:
                self.assertIn(block, text)
            self.assertNotIn("powershell", text)

    def test_fenced_code_pagination_and_tabs(self):
        body = "".join(f"echo line-{i:03d}\n" for i in range(160))
        source = f"```sh\n{body}```"
        docx = Document(io.BytesIO(render_source_copy(source, "docx")))
        self.assertEqual(docx.paragraphs[0].text, body)
        with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
            self.assertGreater(len(pdf), 1)
            self.assertIn(body, "".join(page.get_text() for page in pdf))
        tabbed = "```python\n\tprint('tab')\n```\n"
        docx = Document(io.BytesIO(render_source_copy(tabbed, "docx")))
        self.assertEqual(docx.paragraphs[0].text, "\tprint('tab')\n")
        with self.assertRaises(SourceCopyError):
            render_source_copy(tabbed, "pdf")

    def test_fenced_code_bounds_and_outside_validation(self):
        long_line = "echo " + "x" * 1000
        source = f"```sh\n{long_line}\n```"
        document = Document(io.BytesIO(render_source_copy(source, "docx")))
        self.assertEqual(document.paragraphs[0].text, long_line + "\n")
        with self.assertRaisesRegex(SourceCopyError, "code block whitespace"):
            render_source_copy(source, "pdf")
        cases = [
            "```\n" + "x" * MAX_SOURCE_BYTES + "\n```",
            "```sh\ncommand\n```\n\n<script>bad</script>",
            "```sh\ncommand\n```\n\n![bad](https://example.com/image)",
            "```sh\ncommand\n~~~",
            "- ```sh\n  echo one",
            "```invalid`label\nbody\n```",
        ]
        for source in cases:
            for fmt in ("docx", "pdf"):
                with self.subTest(source=source[:60], fmt=fmt):
                    with self.assertRaises(SourceCopyError):
                        render_source_copy(source, fmt)

    def test_fenced_code_obeys_element_and_pdf_page_limits(self):
        source = "\n\n".join(f"```sh\necho {i}\n```" for i in range(3))
        with patch("functions_document_source_copy.MAX_ELEMENTS", 4):
            for fmt in ("docx", "pdf"):
                with self.assertRaisesRegex(SourceCopyError, "too many fenced"):
                    render_source_copy(source, fmt)
        multi_page = "```sh\n" + "".join(f"echo {i:03d}\n" for i in range(120)) + "```"
        with patch("functions_document_source_copy.MAX_PDF_PAGES", 1):
            with self.assertRaisesRegex(SourceCopyError, "page limit"):
                render_source_copy(multi_page, "pdf")

    def test_fence_crlf_and_unlabeled_blocks(self):
        source = "```\r\n echo café\r\n\r\n echo 中文\r\n```\r\n"
        expected = " echo café\n\n echo 中文\n"
        document = Document(io.BytesIO(render_source_copy(source, "docx")))
        self.assertEqual(document.paragraphs[0].text, expected)
        with fitz.open(stream=render_source_copy(source, "pdf"), filetype="pdf") as pdf:
            self.assertIn(expected.replace("\n\n", "\n \n"), pdf[0].get_text())

    def test_pdf_rejects_unplaceable_table_content(self):
        source = "| Name | Value |\n| --- | --- |\n| " + "x" * 3000 + " | end |"
        with self.assertRaises(SourceCopyError):
            render_source_copy(source, "pdf")

    def test_links_do_not_fetch_resources(self):
        with patch("socket.socket.connect", side_effect=AssertionError("network access")), \
                patch("socket.create_connection", side_effect=AssertionError("network access")):
            for fmt in ("docx", "pdf"):
                result = render_source_copy("[link](https://example.com/file)", fmt)
                self.assertGreater(len(result), 0)

    def test_cold_import_has_no_application_bootstrap_or_network(self):
        probe = """
import socket
import sys
sys.path.insert(0, sys.argv[1])
def blocked(*args, **kwargs):
    raise RuntimeError("Unexpected network access")
socket.socket.connect = blocked
socket.create_connection = blocked
import functions_document_source_copy as renderer
for fmt in ("docx", "pdf"):
    data = renderer.render_document_source_copy("[link](https://example.com)", fmt)
    if not data:
        raise RuntimeError("Missing output")
if "config" in sys.modules or any(name.startswith("route_") for name in sys.modules):
    raise RuntimeError("Application bootstrap imported")
"""
        result = subprocess.run(
            [sys.executable, "-c", probe, str(APP_DIR)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reject_unsafe_unsupported_and_oversize_content(self):
        cases = [
            "![image](https://example.com/image.png)",
            '<img src="https://example.com/image.png">',
            "<script>alert(1)</script>",
            "[bad](javascript:alert(1))",
            "[bad](file:///etc/passwd)",
            "[bad](https://user:pass@example.com/)",
            "[relative](/secret)",
            "[title](https://example.com \"title\")",
            "| A | B |\n|---|---|\n| one | two | lost |",
            "| A | B |\n|---|---|\n| one |",
            "- Parent\n   - ambiguous child",
            "- Parent\n  - Child\n      - skipped level",
            "- Parent\n    - Child\n  - mixed indentation",
            "- Parent\n  - Child\n\n    ambiguous continuation\n  - Sibling",
            "- Parent\n    - Child\n\n        continuation\n    - Sibling",
            "Paragraph\n- Parent\n  - Child",
            "- First\n1. Ambiguous list switch",
            "1) Unsupported numbering",
            "- [x] unsupported task",
            "1. [x] unsupported task",
            "![missing][reference]",
            "[citation][missing]",
            "~~unsupported strikethrough~~",
            "```python\nunclosed",
            "````python\ncode\n```",
            "- list\n    ```sh\n    command\n    ```",
            "> Unsupported quote",
            "9" * 5000 + ". too large",
            "x" * (MAX_SOURCE_BYTES + 1),
            "bad\x00character",
        ]
        for source in cases:
            for fmt in ("docx", "pdf"):
                with self.subTest(source=source[:60], fmt=fmt):
                    with self.assertRaises(SourceCopyError):
                        render_source_copy(source, fmt)

    def test_pdf_rejects_missing_glyph_instead_of_substitution(self):
        with self.assertRaisesRegex(SourceCopyError, "U\\+1F600"):
            render_source_copy("Emoji 😀", "pdf")
        document = Document(io.BytesIO(render_source_copy("Emoji 😀", "docx")))
        self.assertEqual(document.paragraphs[0].text, "Emoji 😀")

    def test_empty_and_invalid_arguments(self):
        result = render_document_source_copy(source_markdown="API", output_format="docx")
        self.assertEqual(Document(io.BytesIO(result)).paragraphs[0].text, "API")
        for fmt in ("docx", "pdf"):
            self.assertTrue(render_source_copy("", fmt))
        for source, fmt in ((None, "pdf"), ("text", "html"), ("text", None)):
            with self.assertRaises(SourceCopyError):
                render_source_copy(source, fmt)


if __name__ == "__main__":
    unittest.main()
