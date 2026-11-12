import csv
import io
import json
import os
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as _xml_escape

import core.timeutil as timeutil
from core.file_dispatch import MIME_MAP, sanitize_filename
from core.json_safe import safe_json_dumps
from timing import IST
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors


def build_contacts_excel(contacts, tasks=None) -> bytes:
    """Builds a formatted multi-sheet Excel spreadsheet containing contacts and tasks."""
    wb = openpyxl.Workbook()
    # Sheet 1: Contacts & Networking
    ws = wb.active
    ws.title = "Contacts & Networking"

    headers = [
        "Full Name", "Company", "Job Title / Role", "Email", "Phone",
        "Met At / Location", "Context & Notes", "Commitments / Action Items", "Saved Date"
    ]
    ws.append(headers)

    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for c in contacts:
        ws.append([
            c.get("name") or "",
            c.get("company") or "",
            c.get("role_title") or "",
            c.get("email") or "",
            c.get("phone") or "",
            c.get("met_at") or "",
            c.get("context_notes") or "",
            c.get("commitments") or "",
            str(c.get("created_at") or "")[:10]
        ])

    for row in ws.iter_rows(min_row=2, max_row=max(ws.max_row, 2), min_col=1, max_col=len(headers)):
        for cell in row:
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center")

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(min(max_len + 4, 45), 14)

    # Sheet 2: Tasks & Reminders (if any)
    if tasks:
        ws_tasks = wb.create_sheet(title="Tasks & Reminders")
        task_headers = ["Category", "Title", "Amount", "Due Date", "Time"]
        ws_tasks.append(task_headers)
        for col_idx in range(1, len(task_headers) + 1):
            cell = ws_tasks.cell(row=1, column=col_idx)
            cell.fill = PatternFill(start_color="2E75B6", end_color="2E75B6", fill_type="solid")
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for t in tasks:
            ws_tasks.append([
                t[0] if len(t) > 0 else "",
                t[1] if len(t) > 1 else "",
                t[2] if len(t) > 2 else "",
                str(t[3]) if len(t) > 3 and t[3] else "",
                str(t[4])[:5] if len(t) > 4 and t[4] else ""
            ])
        for col in ws_tasks.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws_tasks.column_dimensions[col_letter].width = max(min(max_len + 4, 40), 14)

    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _pdf_safe_text(text: Any) -> str:
    """Escape XML special characters for ReportLab Paragraph and preserve line breaks."""
    s = "" if text is None else str(text)
    return _xml_escape(s).replace("\n", "<br/>")


def _parse_text_to_sections(title: str, content: str) -> List[Dict[str, Any]]:
    """Convert raw or Markdown-style text into structured sections without brittle regexes."""
    lines = (content or "").splitlines()
    sections: List[Dict[str, Any]] = []
    cur: Dict[str, Any] = {"heading": "", "paragraphs": [], "bullets": []}

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if cur["heading"] or cur["paragraphs"] or cur["bullets"]:
                sections.append(cur)
                cur = {"heading": "", "paragraphs": [], "bullets": []}
            cur["heading"] = line.lstrip("#").strip()
        elif line.startswith(("- ", "* ", "• ")):
            cur["bullets"].append(line[2:].strip())
        else:
            cur["paragraphs"].append(line)

    if cur["heading"] or cur["paragraphs"] or cur["bullets"]:
        sections.append(cur)
    if not sections and content:
        sections.append({"heading": title or "Document", "paragraphs": [content], "bullets": []})
    return sections


def build_styled_pdf(title: str, sections: list[dict], author: str = "PeculiarAI") -> bytes:
    """
    Builds an executive PDF document with title, timestamp, and styled sections.
    Supports 'heading', 'text', 'paragraphs', 'bullets'/'items', and 'table' keys.
    """
    max_table_cols = max(
        (max((len(r) for r in (sec.get("table") or []) if isinstance(r, (list, tuple))), default=0)
         for sec in (sections or []) if isinstance(sec, dict)),
        default=0,
    )
    use_landscape = max_table_cols >= 6
    page_size = landscape(letter) if use_landscape else letter
    avail_width = 700 if use_landscape else 510

    bio = io.BytesIO()
    doc = SimpleDocTemplate(
        bio,
        pagesize=page_size,
        leftMargin=45,
        rightMargin=45,
        topMargin=45,
        bottomMargin=45
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=22,
        leading=26,
        textColor=colors.HexColor('#1F4E79'),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#595959'),
        spaceAfter=15,
    )
    h2_style = ParagraphStyle(
        'Heading2Custom',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor('#2E75B6'),
        spaceBefore=14,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        'BodyCustom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#262626'),
        spaceAfter=8,
    )
    bullet_style = ParagraphStyle(
        'BulletCustom',
        parent=body_style,
        leftIndent=14,
        spaceAfter=4,
    )

    story = [
        Paragraph(_pdf_safe_text(title or "Document"), title_style),
        Paragraph(f"Prepared by {_pdf_safe_text(author)} • Generated on {timeutil.now(IST).strftime('%d %B %Y, %I:%M %p')}", subtitle_style),
        HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#1F4E79'), spaceBefore=2, spaceAfter=14),
    ]

    for sec in (sections or []):
        if not isinstance(sec, dict):
            story.append(Paragraph(_pdf_safe_text(sec), body_style))
            continue
        if sec.get("heading"):
            story.append(Paragraph(_pdf_safe_text(sec["heading"]), h2_style))
        for p in sec.get("paragraphs", []) or []:
            if str(p).strip():
                story.append(Paragraph(_pdf_safe_text(p), body_style))
        raw_text = sec.get("text")
        if raw_text and str(raw_text).strip():
            for parsed_sec in _parse_text_to_sections("", str(raw_text)):
                if parsed_sec.get("heading"):
                    story.append(Paragraph(_pdf_safe_text(parsed_sec["heading"]), h2_style))
                for p in parsed_sec.get("paragraphs", []):
                    story.append(Paragraph(_pdf_safe_text(p), body_style))
                for b in parsed_sec.get("bullets", []):
                    story.append(Paragraph(f"• {_pdf_safe_text(b)}", bullet_style))
        for b in (sec.get("bullets") or sec.get("items") or []):
            if str(b).strip():
                story.append(Paragraph(f"• {_pdf_safe_text(b)}", bullet_style))

        # Optional table in section
        if sec.get("table"):
            t_data = []
            max_cols = max((len(r) for r in sec["table"] if isinstance(r, (list, tuple))), default=0)
            if max_cols > 0:
                for row_idx, row in enumerate(sec["table"]):
                    padded = list(row) + [""] * (max_cols - len(row))
                    cell_style = ParagraphStyle(
                        'TableHeader' if row_idx == 0 else 'TableCell',
                        parent=body_style,
                        textColor=colors.whitesmoke if row_idx == 0 else colors.HexColor('#262626'),
                        fontName='Helvetica-Bold' if row_idx == 0 else 'Helvetica',
                        fontSize=9,
                        leading=12,
                        spaceAfter=0,
                    )
                    t_data.append([Paragraph(_pdf_safe_text(cell), cell_style) for cell in padded[:max_cols]])
                if t_data:
                    col_max_lens = []
                    for c_idx in range(max_cols):
                        longest = max(
                            (len(str(r[c_idx])) for r in sec["table"] if isinstance(r, (list, tuple)) and c_idx < len(r)),
                            default=6,
                        )
                        col_max_lens.append(max(6, min(longest, 28)))
                    total_w = sum(col_max_lens) or max_cols
                    col_widths = [max(48, (avail_width * w) / total_w) for w in col_max_lens]
                    scale = avail_width / sum(col_widths)
                    col_widths = [w * scale for w in col_widths]

                    table = Table(t_data, colWidths=col_widths, repeatRows=1)
                    table.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F4E79')),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('TOPPADDING', (0, 0), (-1, -1), 5),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#F2F4F7')),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D9D9D9')),
                    ]))
                    story.append(table)
                    story.append(Spacer(1, 10))

    doc.build(story)
    return bio.getvalue()


def _style_worksheet(ws, headers: List[str], header_color: str = "1F4E79") -> None:
    header_fill = PatternFill(start_color=header_color, end_color=header_color, fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )
    if headers:
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.freeze_panes = "A2"

    for row in ws.iter_rows(min_row=2, max_row=max(ws.max_row, 2), min_col=1, max_col=max(len(headers), 1)):
        for cell in row:
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center", wrap_text=True)

    for col in ws.columns:
        max_len = max((len(str(cell.value or '')) for cell in col), default=10)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(min(max_len + 4, 48), 14)


def build_excel_workbook(sheets: List[Dict[str, Any]], title: str = "Export") -> bytes:
    """Build a cleanly styled multi-sheet Excel (.xlsx) workbook for arbitrary structured tables."""
    wb = openpyxl.Workbook()
    used_names = set()

    valid_sheets = sheets or [{"name": title or "Sheet1", "headers": ["Item"], "rows": []}]
    for idx, sh in enumerate(valid_sheets):
        raw_name = str(sh.get("name") or sh.get("title") or f"Sheet{idx + 1}").strip()
        clean_name = "".join(c if c not in '[]:*?/\\' else '_' for c in raw_name)[:31].strip() or f"Sheet{idx + 1}"
        base_name = clean_name
        suffix = 1
        while clean_name in used_names:
            tag = f"_{suffix}"
            clean_name = base_name[: 31 - len(tag)] + tag
            suffix += 1
        used_names.add(clean_name)

        ws = wb.active if idx == 0 else wb.create_sheet(title=clean_name)
        ws.title = clean_name

        headers = [str(h) for h in (sh.get("headers") or [])]
        rows = sh.get("rows") or []
        if not headers and rows and isinstance(rows[0], dict):
            headers = [str(k) for k in rows[0].keys()]
            rows = [[r.get(k, "") for k in headers] for r in rows]

        if headers:
            ws.append(headers)
        for r in rows:
            if isinstance(r, dict) and headers:
                ws.append([r.get(h, "") for h in headers])
            elif isinstance(r, (list, tuple)):
                ws.append(list(r))
            else:
                ws.append([str(r)])

        _style_worksheet(ws, headers, header_color="1F4E79" if idx == 0 else "2E75B6")

    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def build_csv_bytes(headers: List[str], rows: List[List[Any]]) -> bytes:
    """Build a UTF-8 BOM CSV file compatible with Microsoft Excel and Google Sheets."""
    sio = io.StringIO()
    writer = csv.writer(sio)
    if headers:
        writer.writerow([str(h) for h in headers])
    for r in (rows or []):
        if isinstance(r, dict) and headers:
            writer.writerow([r.get(h, "") for h in headers])
        elif isinstance(r, (list, tuple)):
            writer.writerow(list(r))
        else:
            writer.writerow([str(r)])
    return sio.getvalue().encode("utf-8-sig")


def build_pptx_bytes(title: str, slides: List[Dict[str, Any]], subtitle: str = "") -> bytes:
    """Build an executive PowerPoint (.pptx) presentation with title slide and content/table slides."""
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # Slide 1: Title Slide
    title_slide = prs.slides.add_slide(prs.slide_layouts[0])
    if title_slide.shapes.title:
        title_slide.shapes.title.text = title or "Presentation"
    if len(title_slide.placeholders) > 1:
        title_slide.placeholders[1].text = subtitle or f"Prepared by PeculiarAI • {timeutil.now(IST).strftime('%d %B %Y')}"

    content_layout = prs.slide_layouts[1] if len(prs.slide_layouts) > 1 else prs.slide_layouts[0]
    for sl in (slides or []):
        slide = prs.slides.add_slide(content_layout)
        if slide.shapes.title:
            slide.shapes.title.text = str(sl.get("title") or sl.get("heading") or "")

        bullets = list(sl.get("bullets") or sl.get("items") or [])
        for p in (sl.get("paragraphs") or []):
            if str(p).strip():
                bullets.append(str(p).strip())
        if sl.get("text"):
            for ln in str(sl["text"]).splitlines():
                if ln.strip():
                    bullets.append(ln.strip().lstrip("-*• "))

        if bullets and len(slide.placeholders) > 1:
            tf = slide.placeholders[1].text_frame
            tf.clear()
            for idx, b in enumerate(bullets):
                para = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
                para.text = str(b)
                para.font.size = Pt(18)

        table_rows = sl.get("table")
        if table_rows and isinstance(table_rows, list) and len(table_rows) >= 1:
            max_cols = max((len(r) for r in table_rows if isinstance(r, (list, tuple))), default=0)
            if max_cols > 0:
                tbl_shape = slide.shapes.add_table(
                    len(table_rows), max_cols, Inches(0.8), Inches(4.0 if bullets else 1.8), Inches(11.7), Inches(2.5)
                )
                tbl = tbl_shape.table
                for r_i, r_vals in enumerate(table_rows):
                    for c_i in range(max_cols):
                        val = r_vals[c_i] if c_i < len(r_vals) else ""
                        cell = tbl.cell(r_i, c_i)
                        cell.text = str(val)
                        if r_i == 0:
                            cell.fill.solid()
                            cell.fill.fore_color.rgb = RGBColor(0x1F, 0x4E, 0x79)
                            for p in cell.text_frame.paragraphs:
                                p.font.bold = True
                                p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    bio = io.BytesIO()
    prs.save(bio)
    return bio.getvalue()


def build_docx_bytes(title: str, sections: List[Dict[str, Any]], author: str = "PeculiarAI") -> bytes:
    """Build a valid OOXML Microsoft Word (.docx) document using Python stdlib zipfile + XML."""
    body_parts: List[str] = []

    def _p_xml(txt: str, bold: bool = False, size_half_pt: int = 22, color: str = "262626", space_after: int = 120) -> str:
        rpr = f'<w:rPr><w:color w:val="{color}"/><w:sz w:val="{size_half_pt}"/>'
        if bold:
            rpr += "<w:b/>"
        rpr += "</w:rPr>"
        return (
            f'<w:p><w:pPr><w:spacing w:after="{space_after}"/></w:pPr>'
            f'<w:r>{rpr}<w:t xml:space="preserve">{_xml_escape(txt)}</w:t></w:r></w:p>'
        )

    body_parts.append(_p_xml(title or "Document", bold=True, size_half_pt=40, color="1F4E79", space_after=80))
    body_parts.append(
        _p_xml(
            f"Prepared by {author} • {timeutil.now(IST).strftime('%d %B %Y, %I:%M %p')}",
            size_half_pt=18,
            color="595959",
            space_after=220,
        )
    )

    for sec in (sections or []):
        if not isinstance(sec, dict):
            body_parts.append(_p_xml(str(sec)))
            continue
        if sec.get("heading"):
            body_parts.append(_p_xml(str(sec["heading"]), bold=True, size_half_pt=28, color="2E75B6", space_after=100))
        for p in (sec.get("paragraphs") or []):
            if str(p).strip():
                body_parts.append(_p_xml(str(p)))
        if sec.get("text"):
            for parsed_sec in _parse_text_to_sections("", str(sec["text"])):
                if parsed_sec.get("heading"):
                    body_parts.append(_p_xml(parsed_sec["heading"], bold=True, size_half_pt=28, color="2E75B6", space_after=100))
                for p in parsed_sec.get("paragraphs", []):
                    body_parts.append(_p_xml(p))
                for b in parsed_sec.get("bullets", []):
                    body_parts.append(_p_xml(f"• {b}", space_after=80))
        for b in (sec.get("bullets") or sec.get("items") or []):
            if str(b).strip():
                body_parts.append(_p_xml(f"• {b}", space_after=80))
        tbl = sec.get("table")
        if tbl and isinstance(tbl, list):
            rows_xml = []
            for r_idx, row in enumerate(tbl):
                cells_xml = []
                for cell_val in row:
                    shd = '<w:shd w:val="clear" w:color="auto" w:fill="1F4E79"/>' if r_idx == 0 else ""
                    txt_color = "FFFFFF" if r_idx == 0 else "262626"
                    cells_xml.append(
                        f'<w:tc><w:tcPr>{shd}</w:tcPr>{_p_xml(str(cell_val), bold=(r_idx == 0), size_half_pt=20, color=txt_color, space_after=40)}</w:tc>'
                    )
                rows_xml.append(f'<w:tr>{"".join(cells_xml)}</w:tr>')
            body_parts.append(
                '<w:tbl><w:tblPr><w:tblBorders>'
                '<w:top w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
                '<w:left w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
                '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
                '<w:right w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
                '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
                '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
                '</w:tblBorders></w:tblPr>' + "".join(rows_xml) + '</w:tbl>'
            )

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{"".join(body_parts)}<w:sectPr/></w:body></w:document>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )

    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("word/document.xml", document_xml)
    return bio.getvalue()


def build_html_bytes(title: str, sections: List[Dict[str, Any]]) -> bytes:
    """Build a clean, self-contained responsive HTML report."""
    chunks = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>{_xml_escape(title or 'Document')}</title>",
        "<style>body{font-family:system-ui,-apple-system,sans-serif;max-width:820px;margin:40px auto;padding:0 20px;color:#1e293b;line-height:1.6}h1{color:#1F4E79;border-bottom:2px solid #1F4E79;padding-bottom:8px}h2{color:#2E75B6;margin-top:24px}table{border-collapse:collapse;width:100%;margin:16px 0}th{background:#1F4E79;color:#fff;text-align:left;padding:8px}td{border:1px solid #cbd5e1;padding:8px}tr:nth-child(even) td{background:#f8fafc}</style>",
        f"</head><body><h1>{_xml_escape(title or 'Document')}</h1>",
    ]
    for sec in (sections or []):
        if not isinstance(sec, dict):
            chunks.append(f"<p>{_xml_escape(str(sec))}</p>")
            continue
        if sec.get("heading"):
            chunks.append(f"<h2>{_xml_escape(str(sec['heading']))}</h2>")
        for p in (sec.get("paragraphs") or []):
            chunks.append(f"<p>{_xml_escape(str(p))}</p>")
        if sec.get("text"):
            chunks.append(f"<p>{_xml_escape(str(sec['text'])).replace(chr(10), '<br>')}</p>")
        bullets = sec.get("bullets") or sec.get("items") or []
        if bullets:
            chunks.append("<ul>" + "".join(f"<li>{_xml_escape(str(b))}</li>" for b in bullets) + "</ul>")
        tbl = sec.get("table")
        if tbl and isinstance(tbl, list):
            chunks.append("<table>")
            for r_idx, row in enumerate(tbl):
                tag = "th" if r_idx == 0 else "td"
                chunks.append("<tr>" + "".join(f"<{tag}>{_xml_escape(str(c))}</{tag}>" for c in row) + "</tr>")
            chunks.append("</table>")
    chunks.append("</body></html>")
    return "".join(chunks).encode("utf-8")


def build_document_artifact(
    fmt: str,
    title: str = "Document",
    filename: Optional[str] = None,
    content: str = "",
    sections: Optional[List[Dict[str, Any]]] = None,
    slides: Optional[List[Dict[str, Any]]] = None,
    headers: Optional[List[str]] = None,
    rows: Optional[List[List[Any]]] = None,
    sheets: Optional[List[Dict[str, Any]]] = None,
    data: Any = None,
) -> Tuple[bytes, str, str]:
    """
    Universal format-dispatching document builder supporting:
    pdf, xlsx, csv, docx, pptx, md, txt, json, html, ics.
    Returns (file_bytes, safe_filename, mime_type).
    """
    norm_fmt = (fmt or "pdf").strip().lower().lstrip(".")
    alias_map = {
        "excel": "xlsx",
        "xls": "xlsx",
        "spreadsheet": "xlsx",
        "word": "docx",
        "doc": "docx",
        "powerpoint": "pptx",
        "ppt": "pptx",
        "slides": "pptx",
        "markdown": "md",
        "text": "txt",
        "calendar": "ics",
    }
    ext = alias_map.get(norm_fmt, norm_fmt)
    if f".{ext}" not in MIME_MAP and ext not in ("md", "html", "ics"):
        ext = "txt"

    eff_title = (title or "Document").strip() or "Document"
    safe_name = sanitize_filename(filename or eff_title, default_name="document", ext=f".{ext}")
    mime_type = MIME_MAP.get(f".{ext}", "application/octet-stream")

    eff_sections = list(sections or [])
    if not eff_sections and content:
        eff_sections = _parse_text_to_sections(eff_title, content)
    if not eff_sections and headers and rows is not None:
        eff_sections = [{"heading": eff_title, "table": [headers] + list(rows)}]

    if ext == "pdf":
        file_bytes = build_styled_pdf(eff_title, eff_sections or [{"heading": eff_title, "paragraphs": ["Generated by PeculiarAI."]}])
    elif ext == "xlsx":
        if sheets:
            eff_sheets = sheets
        elif headers is not None or rows is not None:
            eff_sheets = [{"name": eff_title[:31], "headers": headers or [], "rows": rows or []}]
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            cols = list(data[0].keys())
            eff_sheets = [{"name": eff_title[:31], "headers": cols, "rows": [[r.get(c, "") for c in cols] for r in data]}]
        else:
            lines = [ln.strip() for ln in (content or "").splitlines() if ln.strip()]
            eff_sheets = [{"name": eff_title[:31], "headers": [eff_title], "rows": [[ln] for ln in lines]}]
        file_bytes = build_excel_workbook(eff_sheets, title=eff_title)
    elif ext == "csv":
        eff_headers = list(headers or [])
        eff_rows = list(rows or [])
        if not eff_headers and isinstance(data, list) and data and isinstance(data[0], dict):
            eff_headers = list(data[0].keys())
            eff_rows = [[r.get(c, "") for c in eff_headers] for r in data]
        elif not eff_headers and not eff_rows and content:
            eff_headers = [eff_title]
            eff_rows = [[ln] for ln in content.splitlines() if ln.strip()]
        file_bytes = build_csv_bytes(eff_headers, eff_rows)
    elif ext == "docx":
        file_bytes = build_docx_bytes(eff_title, eff_sections or [{"heading": eff_title, "paragraphs": [content or ""]}])
    elif ext == "pptx":
        eff_slides = list(slides or [])
        if not eff_slides:
            for sec in eff_sections:
                eff_slides.append({
                    "title": sec.get("heading") or eff_title,
                    "paragraphs": sec.get("paragraphs", []),
                    "bullets": sec.get("bullets") or sec.get("items") or [],
                    "text": sec.get("text", ""),
                    "table": sec.get("table"),
                })
        file_bytes = build_pptx_bytes(eff_title, eff_slides or [{"title": eff_title, "text": content or ""}])
    elif ext == "json":
        payload = data if data is not None else (
            {"title": eff_title, "headers": headers, "rows": rows} if (headers or rows) else {"title": eff_title, "content": content, "sections": eff_sections}
        )
        file_bytes = safe_json_dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    elif ext == "html":
        file_bytes = build_html_bytes(eff_title, eff_sections or [{"heading": eff_title, "text": content}])
    elif ext == "md":
        md_lines = [f"# {eff_title}", ""]
        if content:
            md_lines.append(content)
        else:
            for sec in eff_sections:
                if sec.get("heading"):
                    md_lines.append(f"## {sec['heading']}")
                for p in sec.get("paragraphs", []):
                    md_lines.append(str(p))
                for b in (sec.get("bullets") or sec.get("items") or []):
                    md_lines.append(f"- {b}")
                md_lines.append("")
        file_bytes = "\n".join(md_lines).encode("utf-8")
    else:
        txt_parts = [eff_title, "=" * len(eff_title), ""]
        if content:
            txt_parts.append(content)
        else:
            for sec in eff_sections:
                if sec.get("heading"):
                    txt_parts.append(str(sec["heading"]))
                for p in sec.get("paragraphs", []):
                    txt_parts.append(str(p))
                for b in (sec.get("bullets") or sec.get("items") or []):
                    txt_parts.append(f"- {b}")
                txt_parts.append("")
        file_bytes = "\n".join(txt_parts).encode("utf-8")

    return file_bytes, safe_name, mime_type
