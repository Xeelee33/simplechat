# functions_document_source_copy.py
"""Render already-authorized Markdown without loading application configuration.

``render_document_source_copy(source_markdown, output_format)`` returns DOCX or
PDF bytes.
The caller is responsible for retrieving and authorizing the complete source.
Unsupported markup, unsafe links, excessive input, and unrenderable PDF text
raise ``SourceCopyError`` rather than producing a partial document.
"""

import html
import re
import string
import unicodedata
import uuid
from bisect import bisect_right
from copy import deepcopy
from io import BytesIO
from urllib.parse import urlsplit

import fitz
import markdown
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, Twips
from lxml import etree
from lxml import html as lxml_html
from markdown.extensions.tables import TableExtension, TableProcessor


MAX_SOURCE_BYTES = 200_000
MAX_ELEMENTS = 4_000
MAX_LIST_DEPTH = 8
MAX_TABLE_COLUMNS = 8
MAX_PDF_PAGES = 100


class SourceCopyError(ValueError):
    """The input cannot be represented safely and completely."""


DOCUMENT_TYPOGRAPHY_FIELDS = (
    "title_font", "title_size", "heading_font", "heading_size", "body_font", "body_size",
)


def validate_document_typography(value):
    """Validate bounded DOCX font names and integer point sizes."""
    if not isinstance(value, dict) or set(value) not in (
        set(DOCUMENT_TYPOGRAPHY_FIELDS),
        set(DOCUMENT_TYPOGRAPHY_FIELDS) | {"margin_twips", "line_spacing"},
    ):
        raise SourceCopyError("Document typography is invalid.")
    margin = value.get("margin_twips", 0)
    if (
        not isinstance(margin, int)
        or isinstance(margin, bool)
        or (margin != 0 and not 720 <= margin <= 2880)
        or value.get("line_spacing", "") not in ("", "single", "one_and_half", "double")
    ):
        raise SourceCopyError("Document layout is invalid.")
    for role in ("title", "heading", "body"):
        font = value[f"{role}_font"]
        size = value[f"{role}_size"]
        if (
            not isinstance(font, str)
            or len(font) > 80
            or font != font.strip()
            or any(not char.isprintable() for char in font)
            or any(char in font for char in "<>:/\\")
            or not isinstance(size, int)
            or isinstance(size, bool)
            or (size != 0 and not 6 <= size <= 96)
        ):
            raise SourceCopyError("Document typography is invalid.")
    return dict(value)


def _set_docx_font(font, name, size):
    if name:
        font.name = name
        fonts = font._element.get_or_add_rPr().get_or_add_rFonts()
        for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme", "csTheme"):
            fonts.attrib.pop(qn(f"w:{attribute}"), None)
        fonts.set(qn("w:eastAsia"), name)
        fonts.set(qn("w:cs"), name)
    if size:
        font.size = Pt(size)
        properties = font._element.get_or_add_rPr()
        complex_size = properties.find(qn("w:szCs"))
        if complex_size is None:
            complex_size = OxmlElement("w:szCs")
            properties.insert_element_before(
                complex_size, "w:highlight", "w:u", "w:effect", "w:bdr", "w:shd",
                "w:fitText", "w:vertAlign", "w:rtl", "w:cs", "w:em", "w:lang",
                "w:eastAsianLayout", "w:specVanish", "w:oMath",
            )
        complex_size.set(qn("w:val"), str(size * 2))


def apply_docx_typography(document, typography, title_paragraph=None):
    """Apply validated typography without changing document text."""
    if typography is None:
        return
    typography = validate_document_typography(typography)
    if typography.get("margin_twips"):
        for section in document.sections:
            section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Twips(
                typography["margin_twips"]
            )
    line_spacing = {
        "single": WD_LINE_SPACING.SINGLE,
        "one_and_half": WD_LINE_SPACING.ONE_POINT_FIVE,
        "double": WD_LINE_SPACING.DOUBLE,
    }.get(typography.get("line_spacing", ""))
    if line_spacing is not None:
        paragraphs = list(document.paragraphs)
        for table in document.tables:
            paragraphs.extend(paragraph for row in table.rows for cell in row.cells for paragraph in cell.paragraphs)
        for paragraph in paragraphs:
            paragraph.paragraph_format.line_spacing_rule = line_spacing
    title_font, title_size = typography["title_font"], typography["title_size"]
    if title_font or title_size:
        if title_paragraph is None:
            raise SourceCopyError("The source has no unambiguous title to format.")
        title_style = document.styles.add_style("SimpleChat Document Title", WD_STYLE_TYPE.PARAGRAPH)
        title_style.base_style = document.styles["Heading 1"]
        _set_docx_font(title_style.font, title_font, title_size)
        title_paragraph.style = title_style
    for level in range(1, 7):
        _set_docx_font(
            document.styles[f"Heading {level}"].font,
            typography["heading_font"], typography["heading_size"],
        )
    body_font, body_size = typography["body_font"], typography["body_size"]
    for name in ("Normal", "List Bullet"):
        _set_docx_font(document.styles[name].font, body_font, body_size)
    if body_font or body_size:
        paragraphs = list(document.paragraphs)
        for table in document.tables:
            paragraphs.extend(paragraph for row in table.rows for cell in row.cells for paragraph in cell.paragraphs)
        for paragraph in paragraphs:
            if (
                (title_paragraph is not None and paragraph._p is title_paragraph._p)
                or paragraph.style.name.startswith("Heading ")
            ):
                continue
            for run in paragraph.runs:
                _set_docx_font(run.font, body_font, body_size)


class _StrictTableProcessor(TableProcessor):
    def run(self, parent, blocks):
        rows = blocks[0].split("\n")
        for row in rows[2:]:
            if len(self._split_row(row.strip(" "))) != len(self.separator):
                raise SourceCopyError("Table rows must have the same number of cells.")
        if len(self.separator) > MAX_TABLE_COLUMNS:
            raise SourceCopyError("Table has too many columns.")
        super().run(parent, blocks)
        if len(rows) == 2:
            parent[-1].remove(parent[-1].find("tbody"))


class _StrictTables(TableExtension):
    def extendMarkdown(self, md):
        for char in string.punctuation:
            if char not in md.ESCAPED_CHARS:
                md.ESCAPED_CHARS.append(char)
        md.parser.blockprocessors.register(
            _StrictTableProcessor(md.parser, self.getConfigs()), "table", 75
        )


_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "pre"}
_INLINE_TAGS = {"strong", "em", "code", "a", "br"}
_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);")
_LIST_LINE = re.compile(r"^( *)([-+*]|\d+[.)])\s+")
_TASK_LINE = re.compile(r"^ *(?:[-+*]|\d+[.)]) +\[[ xX]\](?:\s|$)")
_FENCE_OPEN = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_INDENTED_FENCE = re.compile(r"^ {4,}(?:`{3,}|~{3,})")
_LIST_FENCE = re.compile(r"^ {0,3}(?:[-+*]|\d+[.)]) +(?:`{3,}|~{3,})")
_IMAGE = re.compile(r"(?<!\\)(?:\\\\)*!\[")
_STRIKE = re.compile(r"(?<!\\)(?:\\\\)*~~")
_UNRESOLVED_REFERENCE = re.compile(r"(?<!!)\[[^\]\n]+\]\[[^\]\n]*\]")
_PDF_LIGATURE_START = re.compile(r"f(?=[fijlt])")


def _extract_fenced_code(source):
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    parts = source.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    outside = []
    blocks = {}
    index = 0
    while index < len(lines):
        line = lines[index].removesuffix("\n")
        if _INDENTED_FENCE.match(line) or _LIST_FENCE.match(line):
            raise SourceCopyError("Indented or nested fenced code is not supported.")
        opening = _FENCE_OPEN.match(line)
        if not opening:
            outside.append(lines[index])
            index += 1
            continue
        indent, marker, info = opening.groups()
        if marker[0] == "`" and "`" in info:
            raise SourceCopyError("Fenced code language label is invalid.")
        closing = re.compile(rf"^ {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$")
        body = []
        index += 1
        while index < len(lines):
            line = lines[index].removesuffix("\n")
            if closing.fullmatch(line):
                break
            line = lines[index]
            spaces = len(line) - len(line.lstrip(" "))
            body.append(line[min(len(indent), spaces):])
            index += 1
        if index == len(lines):
            raise SourceCopyError("Fenced code block is not closed.")
        if len(blocks) >= MAX_ELEMENTS // 2:
            raise SourceCopyError("Markdown contains too many fenced code blocks.")
        token = f"SCFENCEDCODE{uuid.uuid4().hex.upper()}END"
        while token in source or token in blocks:
            token = f"SCFENCEDCODE{uuid.uuid4().hex.upper()}END"
        blocks[token] = "".join(body)
        outside.append(f"\n\n{token}\n\n")
        index += 1
    return "".join(outside), blocks


def _safe_link(url):
    if not url or any(ord(char) < 33 or ord(char) == 127 for char in url):
        raise SourceCopyError("Link destination is invalid.")
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise SourceCopyError("Only absolute HTTP(S) links are supported.")
        if parts.username is not None or parts.password is not None:
            raise SourceCopyError("Links containing credentials are not supported.")
        _ = parts.port
    except ValueError as exc:
        raise SourceCopyError("Link destination is invalid.") from exc
    return url


def _normalize_list_indentation(source):
    lines = []
    expected_items = []
    active_depth = None
    step = None
    kinds = {}
    after_blank = False
    for line in source.splitlines(keepends=True):
        if not line.strip():
            lines.append(line)
            kinds.clear()
            after_blank = True
            continue
        match = _LIST_LINE.match(line)
        if match:
            indent, marker = match.groups()
            width = len(indent)
            if marker.endswith(")"):
                raise SourceCopyError("Ordered lists must use period markers.")
            if marker[0].isdigit() and len(marker) > 10:
                raise SourceCopyError("Ordered list starting number is too large.")
            if _TASK_LINE.match(line):
                raise SourceCopyError("Task lists are not supported.")
            if width == 0:
                if after_blank:
                    step = None
                depth = 0
            elif active_depth is None:
                if width % 4:
                    raise SourceCopyError("List indentation is ambiguous without a parent item.")
                lines.append(line)
                after_blank = False
                continue
            else:
                if step is None:
                    if width not in (2, 4):
                        raise SourceCopyError("Nested lists require two or four spaces per level.")
                    step = width
                if width % step:
                    raise SourceCopyError("Mixed list indentation is ambiguous.")
                depth = width // step
                if depth > active_depth + 1:
                    raise SourceCopyError("List indentation skips a nesting level.")
            if depth + 1 > MAX_LIST_DEPTH:
                raise SourceCopyError("List nesting exceeds the limit.")
            kind = "ol" if marker[0].isdigit() else "ul"
            if depth in kinds and kinds[depth] != kind:
                raise SourceCopyError("Separate different list types with a blank line.")
            kinds[depth] = kind
            kinds = {level: value for level, value in kinds.items() if level <= depth}
            active_depth = depth
            expected_items.append((kind, depth + 1))
            lines.append(" " * (4 * depth) + line[width:])
        else:
            width = len(line) - len(line.lstrip(" "))
            if active_depth is not None and width and step == 2:
                if after_blank or width != 2 * (active_depth + 1):
                    raise SourceCopyError("List continuation indentation is ambiguous.")
                lines.append(" " * (4 * (active_depth + 1)) + line[width:])
            else:
                if active_depth is not None and after_blank and width == 0:
                    active_depth = None
                    step = None
                    kinds.clear()
                lines.append(line)
        after_blank = False
    return "".join(lines), expected_items


def _parse_source(source):
    if not isinstance(source, str):
        raise SourceCopyError("Markdown source must be a string.")
    try:
        size = len(source.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SourceCopyError("Markdown contains invalid Unicode.") from exc
    if size > MAX_SOURCE_BYTES:
        raise SourceCopyError("Markdown source exceeds the size limit.")
    if any((ord(c) < 32 and c not in "\n\r\t") or 0x7F <= ord(c) <= 0x9F
           or ord(c) in (0xFFFE, 0xFFFF) for c in source):
        raise SourceCopyError("Markdown contains unsupported control characters.")
    outside, fenced_blocks = _extract_fenced_code(source)
    if "\t" in outside:
        raise SourceCopyError("Tabs outside fenced code blocks are not supported.")
    if _IMAGE.search(outside):
        raise SourceCopyError("Images are not supported.")
    if _STRIKE.search(outside):
        raise SourceCopyError("Strikethrough is not supported.")
    outside, expected_items = _normalize_list_indentation(outside)

    parser = markdown.Markdown(extensions=[_StrictTables(), "sane_lists"])
    rendered = parser.convert(outside)
    if any(not _ENTITY.fullmatch(item) for item in parser.htmlStash.rawHtmlBlocks):
        raise SourceCopyError("Embedded HTML is not supported.")
    try:
        root = lxml_html.fragment_fromstring(
            rendered, create_parent="div",
            parser=lxml_html.HTMLParser(recover=False, no_network=True),
        )
    except (etree.ParserError, etree.XMLSyntaxError) as exc:
        raise SourceCopyError("Markdown could not be parsed safely.") from exc
    placeholders = {}
    for element in root:
        if element.tag == "p" and len(element) == 0 and element.text in fenced_blocks:
            if element.text in placeholders:
                raise SourceCopyError("Fenced code block occurs in an unsupported context.")
            placeholders[element.text] = element
    if len(placeholders) != len(fenced_blocks):
        raise SourceCopyError("Fenced code block occurs in an unsupported context.")
    for token, code in fenced_blocks.items():
        placeholder = placeholders[token]
        pre = etree.Element("pre")
        etree.SubElement(pre, "code").text = code
        pre.tail = placeholder.tail
        root.replace(placeholder, pre)
    for element in root.iter():
        if element.text and not (element.tag == "code" and element.getparent().tag == "pre"):
            if _UNRESOLVED_REFERENCE.search(element.text):
                raise SourceCopyError("Unresolved or ambiguous reference link.")
        if element.tail and _UNRESOLVED_REFERENCE.search(element.tail):
            raise SourceCopyError("Unresolved or ambiguous reference link.")
    if len(list(root.iter())) > MAX_ELEMENTS:
        raise SourceCopyError("Markdown contains too many elements.")
    actual_items = [
        (element.getparent().tag,
         sum(ancestor.tag in {"ul", "ol"} for ancestor in element.iterancestors()))
        for element in root.iter("li")
    ]
    if actual_items != expected_items:
        raise SourceCopyError("List indentation or structure is ambiguous.")
    for element in root.iterdescendants():
        tag = element.tag
        if tag not in _BLOCK_TAGS | _INLINE_TAGS | {"li", "thead", "tbody", "tr", "th", "td"}:
            raise SourceCopyError(f"Unsupported Markdown element: {tag}.")
        if tag == "a":
            _safe_link(element.get("href"))
            if element.get("title"):
                raise SourceCopyError("Link titles are not supported.")
        if tag in {"ul", "ol"}:
            depth = sum(ancestor.tag in {"ul", "ol"} for ancestor in element.iterancestors()) + 1
            if depth > MAX_LIST_DEPTH:
                raise SourceCopyError("List nesting exceeds the limit.")
            if element.get("start") and len(element.get("start")) > 9:
                raise SourceCopyError("Ordered list starting number is too large.")
        if tag == "li":
            if any(child.tag not in _INLINE_TAGS | {"p", "ul", "ol"} for child in element):
                raise SourceCopyError("Unsupported content inside a list item.")
        if tag in {"th", "td", "h1", "h2", "h3", "h4", "h5", "h6", "p"}:
            if any(child.tag not in _INLINE_TAGS for child in element):
                raise SourceCopyError("Unsupported nested content.")
        if tag == "pre" and (len(element) != 1 or element[0].tag != "code"):
            raise SourceCopyError("Unsupported code block.")
    return root


def _append_inline(paragraph, element, bold=False, italic=False, code=False):
    def add_text(text, parent=None):
        if not text:
            return
        text = re.sub(r"\n *", " ", text)
        if parent is None:
            run = paragraph.add_run(text)
            run.bold = bold
            run.italic = italic
            if code:
                run.font.name = "Consolas"
        else:
            run = OxmlElement("w:r")
            properties = OxmlElement("w:rPr")
            style = OxmlElement("w:rStyle")
            style.set(qn("w:val"), "Hyperlink")
            properties.append(style)
            if bold:
                properties.append(OxmlElement("w:b"))
            if italic:
                properties.append(OxmlElement("w:i"))
            run.append(properties)
            node = OxmlElement("w:t")
            node.set(qn("xml:space"), "preserve")
            node.text = text
            run.append(node)
            parent.append(run)

    add_text(element.text)
    for child in element:
        if child.tag == "br":
            paragraph.add_run().add_break()
        elif child.tag == "a":
            link = OxmlElement("w:hyperlink")
            link.set(qn("r:id"), paragraph.part.relate_to(
                _safe_link(child.get("href")),
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                is_external=True,
            ))
            # Link labels may contain emphasis; keep their entire visible text.
            add_text("".join(child.itertext()), link)
            paragraph._p.append(link)
        else:
            _append_inline(paragraph, child, bold or child.tag == "strong",
                           italic or child.tag == "em", code or child.tag == "code")
        tail = child.tail
        if child.tag == "br" and tail and tail.startswith("\n"):
            tail = tail[1:]
        add_text(tail)


def _numbering(document, ordered, depth, start):
    numbering = document.part.numbering_part.element
    abstract_ids = [int(item.get(qn("w:abstractNumId"))) for item in numbering.findall(qn("w:abstractNum"))]
    num_ids = [int(item.get(qn("w:numId"))) for item in numbering.findall(qn("w:num"))]
    abstract_id = max(abstract_ids, default=-1) + 1
    num_id = max(num_ids, default=0) + 1
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), "0")
    for name, value in (("start", str(start)), ("numFmt", "decimal" if ordered else "bullet"),
                        ("lvlText", "%1." if ordered else "•"), ("lvlJc", "left")):
        node = OxmlElement(f"w:{name}")
        node.set(qn("w:val"), value)
        level.append(node)
    ppr = OxmlElement("w:pPr")
    indent = OxmlElement("w:ind")
    indent.set(qn("w:left"), str(720 * (depth + 1)))
    indent.set(qn("w:hanging"), "360")
    ppr.append(indent)
    level.append(ppr)
    abstract.append(level)
    first_num = numbering.find(qn("w:num"))
    if first_num is None:
        numbering.append(abstract)
    else:
        numbering.insert(numbering.index(first_num), abstract)
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    reference = OxmlElement("w:abstractNumId")
    reference.set(qn("w:val"), str(abstract_id))
    num.append(reference)
    numbering.append(num)
    return num_id


def _list(document, element, depth=0):
    start = int(element.get("start", "1"))
    num_id = _numbering(document, element.tag == "ol", depth, start)
    for item in element:
        paragraph = document.add_paragraph()
        num_pr = paragraph._p.get_or_add_pPr().get_or_add_numPr()
        num_pr.get_or_add_ilvl().val = 0
        num_pr.get_or_add_numId().val = num_id
        # Render the leading inline sequence independently of nested blocks.
        if item.text and item.text.strip():
            paragraph.add_run(re.sub(r"\n *", " ", item.text))
        for child in item:
            if child.tag in {"ul", "ol"}:
                _list(document, child, depth + 1)
            elif child.tag == "p":
                if paragraph.text.strip():
                    paragraph = document.add_paragraph()
                    paragraph.paragraph_format.left_indent = Inches(0.5 * (depth + 1))
                _append_inline(paragraph, child)
            else:
                wrapper = lxml_html.Element("span")
                wrapper.append(deepcopy(child))
                _append_inline(paragraph, wrapper)
                continue
            if child.tail and child.tail.strip():
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.left_indent = Inches(0.5 * (depth + 1))
                paragraph.add_run(re.sub(r"\n *", " ", child.tail))


def _docx(root, typography=None):
    document = Document()
    title_paragraph = None
    for element in root:
        tag = element.tag
        if tag in {"ul", "ol"}:
            _list(document, element)
        elif tag == "table":
            rows = element.xpath("./thead/tr | ./tbody/tr")
            table = document.add_table(rows=len(rows), cols=len(rows[0]))
            table.style = "Table Grid"
            for row, source_row in zip(table.rows, rows):
                for cell, source_cell in zip(row.cells, source_row):
                    paragraph = cell.paragraphs[0]
                    alignment = source_cell.get("style", "")
                    if "center" in alignment:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    elif "right" in alignment:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                    _append_inline(paragraph, source_cell, bold=source_cell.tag == "th")
        elif tag == "pre":
            paragraph = document.add_paragraph()
            run = paragraph.add_run(element[0].text or "")
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        else:
            paragraph = document.add_heading(level=int(tag[1])) if tag.startswith("h") else document.add_paragraph()
            if element is root[0] and tag == "h1":
                title_paragraph = paragraph
            _append_inline(paragraph, element)
    apply_docx_typography(document, typography, title_paragraph)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _pdf_escape_text(text):
    # Separate potential ligature starts so PDF extraction retains the source characters.
    parts = _PDF_LIGATURE_START.split(text)
    return "<span>f</span>".join(html.escape(part) for part in parts)


def _pdf_html(element):
    tag = element.tag
    if tag == "div":
        return "".join(_pdf_html(child) for child in element)
    if tag == "pre":
        code = _pdf_code_text(element[0].text or "")
        return f"<pre><code>{html.escape(code)}</code></pre>"
    if tag in {"ul", "ol"}:
        start = int(element.get("start", "1"))
        items = []
        for index, item in enumerate(element):
            marker = f"{start + index}. " if tag == "ol" else "• "
            content = _pdf_escape_text(item.text or "")
            for child in item:
                content += _pdf_html(child) + _pdf_escape_text(child.tail or "")
            items.append(f'<div style="margin-left: 18pt">{marker}{content}</div>')
        return "".join(items)
    attrs = ""
    if tag == "a":
        attrs = f' href="{html.escape(_safe_link(element.get("href")), quote=True)}"'
    elif tag == "ol" and element.get("start"):
        attrs = f' start="{int(element.get("start"))}"'
    elif tag in {"th", "td"} and element.get("style") in {
        "text-align: left;", "text-align: center;", "text-align: right;"
    }:
        attrs = f' style="{element.get("style")}"'
    content = _pdf_escape_text(element.text or "")
    for child in element:
        content += _pdf_html(child) + _pdf_escape_text(child.tail or "")
    if tag == "br":
        return "<br/>"
    return f"<{tag}{attrs}>{content}</{tag}>"


def _pdf_code_text(code):
    if "\t" in code:
        raise SourceCopyError("PDF cannot preserve tabs in code blocks.")
    lines = code.split("\n")
    return "\n".join(" " if not line and index < len(lines) - 1 else line
                     for index, line in enumerate(lines))


def _text_fragments(element):
    if element.text:
        yield element.text, element.tag == "code" and element.getparent().tag == "pre"
    for child in element:
        yield from _text_fragments(child)
        if child.tail:
            yield child.tail, False


def _pdf(root):
    font = fitz.Font(fontname="cjk")
    boundaries = sum(len(_PDF_LIGATURE_START.findall(text))
                     for text, is_code in _text_fragments(root) if not is_code)
    if boundaries > 4 * MAX_ELEMENTS:
        raise SourceCopyError("PDF text has too many shaping boundaries.")
    for text in root.itertext():
        for char in text:
            if not char.isspace() and (
                unicodedata.category(char) in {"Cf", "Cc", "Cs"}
                or not font.has_glyph(ord(char))
            ):
                raise SourceCopyError(f"PDF cannot reliably render character U+{ord(char):04X}.")
    css = """
        body { font-family: sans-serif; font-size: 11pt; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 0.5pt solid black; padding: 3pt; }
        pre { white-space: pre-wrap; }
    """
    story = fitz.Story(html=_pdf_html(root), user_css=css)
    page_rect = fitz.paper_rect("letter")
    content_rect = fitz.Rect(45, 45, page_rect.width - 45, page_rect.height - 45)

    def page_layout(page_number, filled):
        if page_number >= MAX_PDF_PAGES:
            raise SourceCopyError("PDF exceeds the page limit or contains an unplaceable block.")
        return page_rect, content_rect, None

    headings = []

    def record_heading(position):
        if position.heading and position.open_close & 1 and position.text:
            previous_level = headings[-1][0] if headings else 0
            level = min(position.heading, previous_level + 1)
            headings.append([level, position.text, position.page_num])

    pdf = story.write_with_links(page_layout, positionfn=record_heading)
    try:
        raw_text = "".join(page.get_text() for page in pdf)
        chars = []
        offsets = []
        for offset, char in enumerate(raw_text):
            if not char.isspace():
                chars.append(char)
                offsets.append(offset + 1)
        extracted = "".join(chars)
        cursor = 0
        raw_cursor = 0
        for text, is_code in _text_fragments(root):
            if is_code:
                expected_code = _pdf_code_text(text)
                index = raw_text.find(expected_code, raw_cursor)
                if index < 0:
                    raise SourceCopyError("PDF could not preserve code block whitespace.")
                raw_cursor = index + len(expected_code)
                cursor = bisect_right(offsets, raw_cursor)
                continue
            expected = "".join(text.split())
            if expected:
                index = extracted.find(expected, cursor)
                if index < 0:
                    raise SourceCopyError("PDF could not preserve all source text.")
                cursor = index + len(expected)
                raw_cursor = offsets[cursor - 1]
        if headings:
            pdf.set_toc(headings)
        return pdf.tobytes()
    finally:
        pdf.close()


def render_document_source_copy(source_markdown: str, output_format: str, typography=None) -> bytes:
    """Return complete DOCX or PDF bytes for already-authorized Markdown.

    ``output_format`` is ``"docx"`` or ``"pdf"`` (case-insensitive).
    No URLs or external resources are fetched. ``SourceCopyError`` rejects
    unsafe links, images, HTML, block quotes, nested fences, task lists, strike-
    through, ambiguous references/list indentation, and content beyond the
    module's size, element, nesting, table-column, or PDF-page limits. Only
    absolute HTTP(S) links are supported. PDF glyph support is conservative;
    content that cannot be laid out and text-extracted intact is rejected.
    Excessive PDF text shaping boundaries are rejected to bound layout work.
    Nested lists use a consistent two- or four-space step per list; ambiguous
    indentation, skipped levels, and ambiguous continuations are rejected.
    Fenced code supports backticks and tildes; language labels are ignored.
    PDF rejects tabs and code lines that cannot be copied intact; empty code
    lines are represented by a single space in PDF text extraction.
    PDF headings have bookmarks but the PDF is not accessibility-tagged.
    """
    if not isinstance(output_format, str) or output_format.lower() not in {"docx", "pdf"}:
        raise SourceCopyError("Output format must be 'docx' or 'pdf'.")
    root = _parse_source(source_markdown)
    if typography is not None and output_format.lower() != "docx":
        raise SourceCopyError("Typography is supported only for Word documents.")
    return _docx(root, typography) if output_format.lower() == "docx" else _pdf(root)
