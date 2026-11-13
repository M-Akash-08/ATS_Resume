"""Claude calls: JD analysis + truthful one-page resume rewrite, returned as structured JSON."""
import json
import os
import re

DEFAULT_MODELS = {"Gemini": "gemini-2.5-flash", "Ollama (local)": "llama3.1:8b", "Claude": "claude-sonnet-5-5"}

SYSTEM = """You are an expert technical recruiter and ATS optimization specialist.
Your single goal: get this candidate's resume SHORTLISTED for the given job, on ONE PAGE.

ABSOLUTE RULES
1. Never invent employers, titles, dates, degrees, certifications, metrics or skills. Only reword, reorder,
   condense and surface what the candidate's resume (or their extra notes) already states or clearly implies.
2. A JD keyword goes in the resume only if the candidate's real experience supports it. Otherwise list it in
   "missing_unsupported" and do NOT add it.
3. Use JD wording verbatim where truthful (e.g. write "Power BI" if the JD does, not "PowerBI"); include both the
   acronym and expansion once where relevant (e.g. "Machine Learning (ML)").
4. Put keywords in context (summary + experience bullets), plus one compact Skills section. No keyword stuffing.
5. Bullets: strong verb + what you did + tool/keyword + quantified result. Keep each bullet <= 24 words
   (max 2 lines). If a bullet lacks a metric, keep it truthful and add a question asking for the number.
6. ONE PAGE: max ~450 words total. Summary <= 3 lines. Most recent role up to 5 bullets, older roles 2-3,
   very old/irrelevant roles 1-2 or omit. Keep education compact. Drop the least JD-relevant content first.
7. Headline should mirror the JD job title only if it is accurate for the candidate.
8. Plain ATS-safe content: no tables, icons, images, first-person pronouns, or emojis.
9. Most JD-relevant content goes first (top third of the page).

OUTPUT: return ONLY one valid JSON object (no markdown fences, no commentary) with exactly this shape:
{
 "jd": {
   "job_title": str, "seniority": str,
   "must_have": [{"keyword": str, "aliases": [str]}],
   "nice_to_have": [{"keyword": str, "aliases": [str]}]
 },
 "missing_unsupported": [str],
 "top_fixes": [str],
 "questions": [str],
 "change_log": [str],
 "cover_letter_opener": str,
 "resume": {
   "name": str, "headline": str,
   "contact": {"email": str, "phone": str, "location": str, "links": [str]},
   "summary": str,
   "skills": [{"category": str, "items": [str]}],
   "experience": [{"title": str, "company": str, "location": str, "dates": str, "bullets": [str]}],
   "projects": [{"name": str, "bullets": [str]}],
   "education": [{"degree": str, "school": str, "dates": str, "details": str}],
   "certifications": [str]
 }
}
Extract 8-20 must_have and 5-15 nice_to_have keywords (hard skills, tools, domain terms, certifications, titles).
"aliases" = synonyms/acronyms/spelling variants a recruiter might search. "change_log" = each major edit and the JD
keyword it targets. "questions" = missing metrics/facts that would strengthen the resume (max 6)."""


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Model did not return JSON.")
    return json.loads(text[start : end + 1])


def _user_msg(resume_text, jd_text, extra_notes):
    nl = "\n"
    return (
        f"<resume>{nl}{resume_text}{nl}</resume>{nl}{nl}"
        f"<job_description>{nl}{jd_text}{nl}</job_description>{nl}{nl}"
        f"<extra_notes_from_candidate>{nl}{extra_notes or '(none)'}{nl}</extra_notes_from_candidate>{nl}{nl}"
        "Produce the JSON now."
    )


def _call(provider, user, api_key, model, host):
    if provider == "Gemini":
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key or os.environ.get("GEMINI_API_KEY"))
        r = client.models.generate_content(
            model=model, contents=user,
            config=types.GenerateContentConfig(system_instruction=SYSTEM, response_mime_type="application/json",
                                               max_output_tokens=8000, temperature=0.3))
        return r.text or ""
    if provider == "Ollama (local)":
        import urllib.request
        body = json.dumps({"model": model, "stream": False, "format": "json",
                           "options": {"temperature": 0.3, "num_ctx": 16384},
                           "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request((host or "http://localhost:11434") + "/api/chat", body,
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read())["message"]["content"]
    import anthropic
    client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(model=model, max_tokens=8000, system=SYSTEM,
                                 messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def tailor(resume_text, jd_text, extra_notes, api_key, model, provider="Gemini", host=None) -> dict:
    user = _user_msg(resume_text, jd_text, extra_notes)
    last_err = None
    for _ in range(2):
        try:
            data = _extract_json(_call(provider, user, api_key, model, host))
            if "resume" not in data or "jd" not in data:
                raise ValueError("JSON missing 'resume' or 'jd'.")
            return data
        except Exception as e:  # retry once on malformed output
            last_err = e
    raise RuntimeError(f"Could not get valid output from {provider}: {last_err}")
