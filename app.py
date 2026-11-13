import io
import os
import re

import pdfplumber
import streamlit as st
from docx import Document
from reportlab.lib.pagesizes import A4, letter

import ats
import llm
import render

st.set_page_config(page_title="Resume Shortlister", page_icon="📄", layout="wide")
st.title("📄 Resume Shortlister")
st.caption("Upload your resume + paste the job description → get a truthful, keyword-matched, "
           "ATS-friendly **one-page** resume as PDF and Word.")


def read_resume(f) -> str:
    name = f.name.lower()
    data = f.read()
    if name.endswith(".pdf"):
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    if name.endswith(".docx"):
        d = Document(io.BytesIO(data))
        lines = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                lines.append(" | ".join(c.text for c in row.cells))
        return "\n".join(lines)
    return data.decode("utf-8", errors="ignore")


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_") or "Resume"


with st.sidebar:
    st.header("Settings")
    provider = st.selectbox("AI provider", ["Gemini", "Ollama (local)", "Claude"])
    envname = {"Gemini": "GEMINI_API_KEY", "Claude": "ANTHROPIC_API_KEY"}.get(provider)
    api_key = ""
    if envname:
        api_key = st.text_input(f"{provider} API key", type="password", value=os.environ.get(envname, ""),
                                help=f"Free Gemini key: aistudio.google.com. Or set {envname}.")
    host = st.text_input("Ollama host", "http://localhost:11434") if provider == "Ollama (local)" else None
    model = st.text_input("Model", value=llm.DEFAULT_MODELS[provider], key="model_" + provider)
    paper = st.radio("Paper size", ["A4", "Letter"], horizontal=True)
    st.markdown("---")
    st.markdown("**Honesty guard:** the AI only rewords what's already true. Skills you don't have are listed as "
                "gaps, never added.")

c1, c2 = st.columns(2)
with c1:
    up = st.file_uploader("1) Your current resume (PDF / DOCX / TXT)", type=["pdf", "docx", "txt"])
    pasted = st.text_area("…or paste resume text", height=120)
with c2:
    jd = st.text_area("2) Job description", height=250, placeholder="Paste the full job description here")

extra = st.text_area("3) Optional: extra facts / answers (metrics, tools you used, achievements not in your resume)",
                     height=90, placeholder="e.g. Reduced reporting TAT from 3 days to 4 hours; managed team of 5; "
                                            "used Power BI daily…")

go = st.button("🚀 Generate shortlist-ready resume", type="primary", use_container_width=True)

if go:
    resume_text = read_resume(up) if up else pasted
    if not resume_text.strip() or not jd.strip():
        st.error("Please provide both your resume and the job description.")
    elif envname and not (api_key or os.environ.get(envname)):
        st.error(f"Add your {provider} API key in the sidebar.")
    else:
        try:
            with st.spinner("Analyzing the JD and rewriting your resume…"):
                res = llm.tailor(resume_text, jd, extra, api_key, model, provider, host)
            with st.spinner("Fitting to one page and building PDF + Word…"):
                pdf, fitted, params, notes = render.fit_one_page(res["resume"], A4 if paper == "A4" else letter)
                docx_bytes = render.build_docx(fitted, params)
            must, nice = res["jd"]["must_have"], res["jd"]["nice_to_have"]
            before = ats.score(resume_text, must, nice)
            after = ats.score(ats.resume_to_text(fitted), must, nice)
            st.session_state["out"] = dict(res=res, pdf=pdf, docx=docx_bytes, notes=notes, before=before, after=after,
                                           fitted=fitted)
        except Exception as e:
            st.error(f"Something went wrong: {e}")

out = st.session_state.get("out")
if out:
    res, b, a = out["res"], out["before"], out["after"]
    st.success(f"Ready for: **{res['jd'].get('job_title','')}**")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Estimated ATS score", f"{a['total']}/100", f"{a['total']-b['total']:+d} vs original")
    m2.metric("Must-have keywords", f"{a['must_cov']}%", f"{a['must_cov']-b['must_cov']:+d}")
    m3.metric("Nice-to-have keywords", f"{a['nice_cov']}%", f"{a['nice_cov']-b['nice_cov']:+d}")
    m4.metric("Format / parse score", f"{a['format']}%", f"{a['format']-b['format']:+d}")
    st.caption("Scores are local estimates from keyword coverage + format checks. Real ATS vendors differ; "
               "treat this as a guide, not a guarantee.")

    name = safe_name(out["fitted"].get("name", "Resume")) + "_" + safe_name(res["jd"].get("job_title", "Role"))
    d1, d2 = st.columns(2)
    d1.download_button("⬇️ Download PDF (1 page)", out["pdf"], f"{name}.pdf", "application/pdf",
                       use_container_width=True)
    d2.download_button("⬇️ Download Word (.docx)", out["docx"], f"{name}.docx",
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       use_container_width=True)
    for n in out["notes"]:
        st.info(n)

    t1, t2, t3, t4 = st.tabs(["Preview", "Keywords", "Fixes & questions", "Change log & cover letter"])
    with t1:
        import base64
        b64 = base64.b64encode(out["pdf"]).decode()
        st.markdown(f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="900"></iframe>',
                    unsafe_allow_html=True)
    with t2:
        k1, k2 = st.columns(2)
        k1.markdown("**✅ Must-have matched**\n\n" + (", ".join(a["must_matched"]) or "-"))
        k1.markdown("**✅ Nice-to-have matched**\n\n" + (", ".join(a["nice_matched"]) or "-"))
        k2.markdown("**❌ Must-have still missing**\n\n" + (", ".join(a["must_missing"]) or "None 🎉"))
        k2.markdown("**❌ Nice-to-have missing**\n\n" + (", ".join(a["nice_missing"]) or "None 🎉"))
        if res.get("missing_unsupported"):
            st.warning("Real gaps (NOT added to your resume because your background doesn't show them):\n\n- "
                       + "\n- ".join(res["missing_unsupported"]))
    with t3:
        st.markdown("**Top fixes**")
        for x in res.get("top_fixes", []): st.markdown(f"- {x}")
        st.markdown("**Questions – answer these in box 3 above and regenerate for a stronger resume**")
        for x in res.get("questions", []): st.markdown(f"- {x}")
        if a["issues"]:
            st.markdown("**Format issues detected**")
            for x in a["issues"]: st.markdown(f"- {x}")
    with t4:
        st.markdown("**Change log**")
        for x in res.get("change_log", []): st.markdown(f"- {x}")
        st.markdown("**Cover letter opener**")
        st.write(res.get("cover_letter_opener", ""))
