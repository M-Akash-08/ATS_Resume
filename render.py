"""Render a structured resume to a guaranteed ONE-PAGE PDF and matching DOCX.

Layout is fitted with reportlab (measured page count). The same font sizes/margins/content are then
used for the DOCX. Fonts: Calibri if present (Windows) so PDF and DOCX match; else Arial/Helvetica.
"""
import copy
import io
import os
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.flowables import HRFlowable

ACCENT = "1F3A5F"
FONT_SIZES = [10.5, 10, 9.5, 9, 8.5, 8]
MARGINS = [0.6, 0.55, 0.5, 0.45, 0.4]  # inches, tried in tandem with font sizes (index-aligned, clamped)

# ---------- fonts ----------
_FONT = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica")
_DOCX_FONT = "Calibri"
_UNICODE_OK = False


def _register_fonts():
    global _FONT, _DOCX_FONT, _UNICODE_OK
    fd = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for fam, files, docx_name in (
        ("Calibri", ("calibri.ttf", "calibrib.ttf", "calibrii.ttf"), "Calibri"),
        ("Arial", ("arial.ttf", "arialbd.ttf", "ariali.ttf"), "Arial"),
    ):
        paths = [os.path.join(fd, f) for f in files]
        if all(os.path.exists(p) for p in paths):
            try:
                pdfmetrics.registerFont(TTFont(fam, paths[0]))
                pdfmetrics.registerFont(TTFont(fam + "-B", paths[1]))
                pdfmetrics.registerFont(TTFont(fam + "-I", paths[2]))
                _FONT = (fam, fam + "-B", fam + "-I", fam)
                _DOCX_FONT = docx_name
                _UNICODE_OK = True
                return
            except Exception:
                pass
    _DOCX_FONT = "Arial"


_register_fonts()

_REPL = {"\u20b9": "Rs. ", "\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
         "\u2022": "-", "\u2192": "->", "\u00a0": " "}


def _t(s) -> str:
    s = "" if s is None else str(s)
    if not _UNICODE_OK:
        for k, v in _REPL.items():
            s = s.replace(k, v)
        s = s.encode("latin-1", "replace").decode("latin-1")
    return s


# ---------- helpers ----------
def _contact_line(r):
    c = r.get("contact", {})
    return "  |  ".join(x for x in [c.get("location"), c.get("phone"), c.get("email"), *c.get("links", [])] if x)


def _skills_lines(r):
    return [(s.get("category", ""), ", ".join(s.get("items", []))) for s in r.get("skills", []) if s.get("items")]


# ---------- PDF ----------
def _build_pdf_once(r, fs, margin, pagesize):
    buf = io.BytesIO()
    over = {"flag": False}

    def later(canvas, doc):
        over["flag"] = True

    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=margin * inch, rightMargin=margin * inch,
                            topMargin=margin * inch, bottomMargin=margin * inch)
    reg, bold, ital, _ = _FONT
    base = ParagraphStyle("base", fontName=reg, fontSize=fs, leading=fs * 1.22, textColor=HexColor("#111111"))
    name = ParagraphStyle("name", parent=base, fontName=bold, fontSize=fs + 8, leading=fs + 10,
                          alignment=TA_CENTER, textColor=HexColor("#" + ACCENT))
    head = ParagraphStyle("head", parent=base, alignment=TA_CENTER, fontName=bold, fontSize=fs + 0.5)
    ctr = ParagraphStyle("ctr", parent=base, alignment=TA_CENTER, fontSize=fs - 0.5)
    sec = ParagraphStyle("sec", parent=base, fontName=bold, fontSize=fs + 0.5, textColor=HexColor("#" + ACCENT),
                         spaceBefore=fs * 0.55, spaceAfter=1)
    bullet = ParagraphStyle("b", parent=base, leftIndent=11, bulletIndent=2, alignment=TA_JUSTIFY)
    summ = ParagraphStyle("s", parent=base, alignment=TA_JUSTIFY)
    right = ParagraphStyle("r", parent=base, alignment=2, fontName=ital)
    avail = pagesize[0] - 2 * margin * inch

    S = []

    def section(title):
        S.append(Paragraph(escape(_t(title.upper())), sec))
        S.append(HRFlowable(width="100%", thickness=0.6, color=HexColor("#" + ACCENT), spaceAfter=2, spaceBefore=0))

    S.append(Paragraph(escape(_t(r.get("name", ""))), name))
    if r.get("headline"):
        S.append(Paragraph(escape(_t(r["headline"])), head))
    S.append(Paragraph(escape(_t(_contact_line(r))), ctr))

    if r.get("summary"):
        section("Professional Summary")
        S.append(Paragraph(escape(_t(r["summary"])), summ))

    if _skills_lines(r):
        section("Skills")
        for cat, items in _skills_lines(r):
            txt = (f"<b>{escape(_t(cat))}:</b> " if cat else "") + escape(_t(items))
            S.append(Paragraph(txt, base))

    if r.get("experience"):
        section("Experience")
        for e in r["experience"]:
            left = Paragraph(f"<b>{escape(_t(e.get('title','')))}</b>"
                             + (f", {escape(_t(e.get('company','')))}" if e.get("company") else "")
                             + (f" - {escape(_t(e.get('location','')))}" if e.get("location") else ""), base)
            t = Table([[left, Paragraph(escape(_t(e.get("dates", ""))), right)]],
                      colWidths=[avail * 0.74, avail * 0.26])
            t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                   ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            S.append(t)
            for b in e.get("bullets", []):
                S.append(Paragraph(escape(_t(b)), bullet, bulletText="\u2022" if _UNICODE_OK else "-"))

    if r.get("projects"):
        section("Projects")
        for p in r["projects"]:
            S.append(Paragraph(f"<b>{escape(_t(p.get('name','')))}</b>", base))
            for b in p.get("bullets", []):
                S.append(Paragraph(escape(_t(b)), bullet, bulletText="\u2022" if _UNICODE_OK else "-"))

    if r.get("education"):
        section("Education")
        for e in r["education"]:
            left = Paragraph(f"<b>{escape(_t(e.get('degree','')))}</b>"
                             + (f", {escape(_t(e.get('school','')))}" if e.get("school") else "")
                             + (f" - {escape(_t(e.get('details','')))}" if e.get("details") else ""), base)
            t = Table([[left, Paragraph(escape(_t(e.get("dates", ""))), right)]],
                      colWidths=[avail * 0.78, avail * 0.22])
            t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                   ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            S.append(t)

    if r.get("certifications"):
        section("Certifications")
        S.append(Paragraph(escape(_t("  |  ".join(r["certifications"]))), base))

    doc.build(S, onLaterPages=later)
    return buf.getvalue(), not over["flag"]


def _trim_one(r) -> bool:
    """Remove the least valuable item. Returns False if nothing left to trim."""
    exp = r.get("experience", [])
    # 1) drop projects first, then certifications beyond 3
    if r.get("projects"):
        p = r["projects"]
        if len(p[-1].get("bullets", [])) > 1:
            p[-1]["bullets"].pop()
        else:
            p.pop()
        return True
    if len(r.get("certifications", [])) > 3:
        r["certifications"].pop()
        return True
    # 2) trim bullets from the oldest role that still has >2 bullets
    for e in reversed(exp):
        if len(e.get("bullets", [])) > 2:
            e["bullets"].pop()
            return True
    # 3) trim to 1 bullet on older roles (not the most recent)
    for e in reversed(exp[1:]):
        if len(e.get("bullets", [])) > 1:
            e["bullets"].pop()
            return True
    # 4) trim most-recent role down to 3
    if exp and len(exp[0].get("bullets", [])) > 3:
        exp[0]["bullets"].pop()
        return True
    return False


def fit_one_page(resume: dict, pagesize=A4):
    """Return (pdf_bytes, fitted_resume, params, notes). Guarantees 1 page (trimming content if needed)."""
    r = copy.deepcopy(resume)
    notes, trimmed = [], 0
    while True:
        for i, fs in enumerate(FONT_SIZES):
            m = MARGINS[min(i, len(MARGINS) - 1)]
            pdf, ok = _build_pdf_once(r, fs, m, pagesize)
            if ok:
                if trimmed:
                    notes.append(f"Trimmed {trimmed} lowest-priority item(s) to keep it on one page.")
                if fs < 10:
                    notes.append(f"Font reduced to {fs}pt to fit one page.")
                return pdf, r, {"fs": fs, "margin": m, "pagesize": pagesize}, notes
        if not _trim_one(r):
            notes.append("Could not fully fit on one page - shorten the content.")
            return pdf, r, {"fs": FONT_SIZES[-1], "margin": MARGINS[-1], "pagesize": pagesize}, notes
        trimmed += 1


# ---------- DOCX ----------
def _bottom_border(par, color=ACCENT):
    pPr = par._p.get_or_add_pPr()
    b = OxmlElement("w:pBdr")
    bt = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", color)):
        bt.set(qn(k), v)
    b.append(bt)
    pPr.append(b)


def _fmt(par, before=0, after=0, line=1.0):
    pf = par.paragraph_format
    pf.space_before, pf.space_after, pf.line_spacing = Pt(before), Pt(after), line


def build_docx(r: dict, params: dict) -> bytes:
    fs, m, ps = params["fs"], params["margin"], params["pagesize"]
    d = Document()
    sec = d.sections[0]
    sec.page_width, sec.page_height = (Inches(ps[0] / 72), Inches(ps[1] / 72))
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Inches(m)
    st = d.styles["Normal"]
    st.font.name, st.font.size = _DOCX_FONT, Pt(fs)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), _DOCX_FONT)
    width = ps[0] / 72 - 2 * m

    def run(p, text, bold=False, italic=False, size=None, color=None):
        rn = p.add_run(text)
        rn.bold, rn.italic = bold, italic
        rn.font.name = _DOCX_FONT
        rn.font.size = Pt(size or fs)
        if color:
            rn.font.color.rgb = RGBColor.from_string(color)
        return rn

    def center(text, **kw):
        p = d.add_paragraph()
        p.alignment = 1
        _fmt(p)
        run(p, text, **kw)
        return p

    def section(title):
        p = d.add_paragraph()
        _fmt(p, before=fs * 0.55, after=1)
        run(p, title.upper(), bold=True, size=fs + 0.5, color=ACCENT)
        _bottom_border(p)

    def two_col(left_parts, right_text):
        p = d.add_paragraph()
        _fmt(p, before=1)
        p.paragraph_format.tab_stops.add_tab_stop(Inches(width), WD_TAB_ALIGNMENT.RIGHT)
        for txt, b in left_parts:
            run(p, txt, bold=b)
        run(p, "\t" + right_text, italic=True)

    def bullet(text):
        p = d.add_paragraph()
        _fmt(p)
        p.alignment = 3
        p.paragraph_format.left_indent = Inches(0.17)
        p.paragraph_format.first_line_indent = Inches(-0.13)
        run(p, "\u2022 " + text)

    center(r.get("name", ""), bold=True, size=fs + 8, color=ACCENT)
    if r.get("headline"):
        center(r["headline"], bold=True, size=fs + 0.5)
    center(_contact_line(r), size=fs - 0.5)

    if r.get("summary"):
        section("Professional Summary")
        p = d.add_paragraph(); _fmt(p); p.alignment = 3
        run(p, r["summary"])

    if _skills_lines(r):
        section("Skills")
        for cat, items in _skills_lines(r):
            p = d.add_paragraph(); _fmt(p)
            if cat:
                run(p, cat + ": ", bold=True)
            run(p, items)

    if r.get("experience"):
        section("Experience")
        for e in r["experience"]:
            left = [(e.get("title", ""), True)]
            if e.get("company"): left.append((", " + e["company"], False))
            if e.get("location"): left.append((" - " + e["location"], False))
            two_col(left, e.get("dates", ""))
            for b in e.get("bullets", []):
                bullet(b)

    if r.get("projects"):
        section("Projects")
        for pr in r["projects"]:
            p = d.add_paragraph(); _fmt(p); run(p, pr.get("name", ""), bold=True)
            for b in pr.get("bullets", []):
                bullet(b)

    if r.get("education"):
        section("Education")
        for e in r["education"]:
            left = [(e.get("degree", ""), True)]
            if e.get("school"): left.append((", " + e["school"], False))
            if e.get("details"): left.append((" - " + e["details"], False))
            two_col(left, e.get("dates", ""))

    if r.get("certifications"):
        section("Certifications")
        p = d.add_paragraph(); _fmt(p)
        run(p, "  |  ".join(r["certifications"]))

    out = io.BytesIO()
    d.save(out)
    return out.getvalue()
