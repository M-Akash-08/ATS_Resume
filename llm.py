"""LLM calls: JD analysis + one-page resume rewrite in one of three modes, returned as structured JSON.

Modes
  honest  - only what the resume / candidate notes support.
  stretch - aggressive but defensible: adjacent skills and stack equivalents, every stretch flagged.
  invent  - mirrors the JD even where the resume has no evidence; every invented claim flagged.
"""
import json
import os
import re

DEFAULT_MODELS = {"Gemini": "gemini-2.5-flash", "Ollama (local)": "llama3.1:8b", "Claude": "claude-sonnet-5-5"}

MODES = {
    "honest": "Honest",
    "stretch": "Stretch",
    "invent": "Invent (max JD match)",
}

# ----------------------------------------------------------------------------------------------
# Prompt pieces
# ----------------------------------------------------------------------------------------------
ROLE = """You are a senior technical recruiter and ATS specialist who has screened thousands of resumes.
Goal: rewrite the candidate's resume so it gets SHORTLISTED for the job description, on exactly ONE PAGE.
A resume is shortlisted when (a) a recruiter's keyword search finds it and (b) a 10-second skim of the top
third shows an obvious match to the role.

Work in this order:
1. Read the JD. Identify the primary tech stack / domain, the must-have and nice-to-have keywords, and the
   3 things this hiring manager cares about most.
2. Read the resume and the candidate's extra notes. These two are the ONLY source of truth about the candidate.
3. Classify every JD keyword as: PROVEN (stated in resume/notes), ADJACENT (not stated, but a close equivalent
   is proven - e.g. Flask proven, Spring Boot asked), or ABSENT (no related evidence).
4. Rewrite the resume following the MODE RULES and PROJECT STACK RULES below.
5. Fill the reporting fields honestly - they are how the candidate learns what was changed."""

MODE_RULES = {
    "honest": """MODE = HONEST
- Use PROVEN keywords only. ADJACENT and ABSENT keywords must NOT appear as skills the candidate has.
- Reword, reorder, condense and surface what is already true. Never add a skill, tool, metric or
  responsibility that the resume/notes do not state or clearly imply.
- Put ADJACENT and ABSENT keywords in "missing_unsupported".
- "unverified_claims" MUST be an empty list.""",

    "stretch": """MODE = STRETCH (aggressive but defensible)
- PROVEN keywords: use freely and prominently.
- ADJACENT keywords: you MAY include them, but only with honest framing, for example:
    * in a bullet next to the real tool: "built REST APIs in Flask (same MVC/controller-service pattern as Spring Boot)"
    * in a separate Skills category named "Familiar With" (never mixed into the main skill categories).
  Never state or imply the candidate shipped production work in an ADJACENT tool.
- ABSENT keywords: do NOT add. List them in "missing_unsupported".
- You may sharpen wording and emphasis strongly, but do not invent metrics, responsibilities or projects.
- EVERY adjacent keyword you include must be listed in "unverified_claims" so the candidate can confirm or
  remove it, and must have an "interview_prep" entry.""",

    "invent": """MODE = INVENT (maximum JD match)
- The candidate has explicitly asked for a resume that mirrors the JD even where their resume has no evidence.
- You MAY add ADJACENT and ABSENT skills/tools to Skills, the summary and bullets, and rewrite bullets and
  projects so they read as work done in the JD's stack.
- Keep it internally consistent and plausible for the candidate's real seniority and years of experience:
  no tool listed that the timeline cannot support, no senior-architect claims on a junior profile.
- Prefer transforming REAL work (same project, same outcome, JD's stack) over inventing work from nothing.
- STILL FORBIDDEN, no exceptions (these are checked in background verification): inventing or altering
  employers, job titles, employment dates, degrees, institutions, grades or certifications.
- Reuse the candidate's real metrics. If you introduce a number that is not in the resume/notes, flag it.
- EVERY claim that is not supported by the resume/notes must be listed in "unverified_claims" - no omissions.
  The candidate relies on this list to know exactly what they must be able to defend in an interview.
- Give an "interview_prep" entry for every invented or swapped skill.
- "missing_unsupported" lists the JD keywords that had no real evidence (even though you added them).""",
}

STACK_RULES = {
    "honest": """PROJECT STACK RULES (JD stack differs from the candidate's project stack, e.g. Python projects vs Java JD)
- Keep each project's REAL stack named. Do not claim the JD's language/framework.
- Rewrite project and experience bullets to lead with what transfers: architecture, design patterns, REST APIs,
  OOP, data modelling, concurrency, testing, CI/CD, cloud, scale and measurable outcomes - using the JD's
  wording for those concepts.
- Record each affected project in "stack_mapping" with presented_stack equal to original_stack.""",

    "stretch": """PROJECT STACK RULES (JD stack differs from the candidate's project stack, e.g. Python projects vs Java JD)
- Keep each project's REAL stack named, and pair it with the JD-stack equivalent so the keyword is present
  and the claim stays true, e.g. "Flask REST service (architecture maps directly to Spring Boot:
  controllers, services, repositories)", "pytest suite (JUnit-style unit + integration tests)".
- Lead bullets with transferable engineering concepts in the JD's wording.
- Record each affected project in "stack_mapping" (original_stack, presented_stack = how it is worded now).""",

    "invent": """PROJECT STACK RULES (JD stack differs from the candidate's project stack, e.g. Python projects vs Java JD)
- Re-stack each relevant project to the JD's language and ecosystem using the closest real equivalents, e.g.
  Python->Java, Flask/FastAPI/Django->Spring Boot, SQLAlchemy->Hibernate/JPA, pytest->JUnit/Mockito,
  pip/poetry->Maven/Gradle, Celery->Spring Batch/Kafka consumers, pandas ETL->Java Streams/Spring Batch.
  Apply the same idea for any other language pair (Java->Python, JS->Go, etc.).
- Keep the project's purpose, scope, outcome and real metrics unchanged - only the technology changes - so the
  candidate can still talk about the problem they actually solved.
- Use tools that genuinely fit together; do not mix incompatible ecosystems.
- Record EVERY swap in "stack_mapping" and in "unverified_claims".""",
}

WRITING_RULES = """WRITING RULES (all modes)
- Use the JD's exact wording for keywords ("Power BI" not "PowerBI"); give acronym + expansion once where
  useful ("Machine Learning (ML)").
- Keywords must appear in context (summary and bullets) and once in a compact Skills section. No stuffing,
  no keyword lists dumped into bullets.
- Bullets: strong verb + what was done + tool/keyword + quantified result. Max 24 words each (2 lines).
- ONE PAGE: about 450 words total. Summary max 3 lines. Most recent role up to 5 bullets, older roles 2-3,
  old or irrelevant roles 1-2 or omit. At most 2 projects with 2 bullets each. Compact education.
  Drop the least JD-relevant content first.
- Most JD-relevant content first. The headline mirrors the JD title only if it fits the candidate's level.
- Plain ATS-safe text: no tables, icons, emojis, or first-person pronouns."""

OUTPUT = """OUTPUT
Return ONLY one valid JSON object - no markdown fences, no commentary - with exactly this shape:
{
 "jd": {
   "job_title": str, "seniority": str, "primary_stack": str,
   "must_have": [{"keyword": str, "aliases": [str]}],
   "nice_to_have": [{"keyword": str, "aliases": [str]}]
 },
 "missing_unsupported": [str],
 "unverified_claims": [{"claim": str, "where": str, "risk": "low" | "medium" | "high"}],
 "stack_mapping": [{"project": str, "original_stack": str, "presented_stack": str, "note": str}],
 "interview_prep": [{"topic": str, "why": str, "prep": str}],
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
Field notes
- must_have: 8-20 items, nice_to_have: 5-15 items (hard skills, tools, domain terms, certifications).
  "aliases" = synonyms, acronyms and spelling variants a recruiter might search.
- unverified_claims.where = the resume section/bullet containing the claim; risk = how likely an interviewer
  is to probe it and expose a gap.
- interview_prep.prep = the specific things to learn or practise to defend that claim (concrete, 1-2 lines).
- questions: up to 6 missing facts/metrics that would make the resume stronger.
- change_log: each major edit and the JD keyword it targets.
- Use [] for any list with nothing to report. Never omit a key."""


def build_system(mode: str) -> str:
    if mode not in MODE_RULES:
        raise ValueError(f"Unknown mode: {mode}")
    return "\n\n".join([ROLE, MODE_RULES[mode], STACK_RULES[mode], WRITING_RULES, OUTPUT])


def build_user(resume_text: str, jd_text: str, extra_notes: str, mode: str) -> str:
    nl = "\n"
    return (
        f"<resume>{nl}{resume_text}{nl}</resume>{nl}{nl}"
        f"<job_description>{nl}{jd_text}{nl}</job_description>{nl}{nl}"
        f"<candidate_notes>{nl}{extra_notes or '(none)'}{nl}</candidate_notes>{nl}{nl}"
        f"Mode: {mode.upper()}. Follow the mode rules and project stack rules exactly, then return the JSON."
    )


# ----------------------------------------------------------------------------------------------
# Providers
# ----------------------------------------------------------------------------------------------
def _call(provider, system, user, api_key, model, host):
    if provider == "Gemini":
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key or os.environ.get("GEMINI_API_KEY"))
        r = client.models.generate_content(
            model=model, contents=user,
            config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                               max_output_tokens=16000, temperature=0.3))
        return r.text or ""
    if provider == "Ollama (local)":
        import urllib.request
        body = json.dumps({"model": model, "stream": False, "format": "json",
                           "options": {"temperature": 0.3, "num_ctx": 16384},
                           "messages": [{"role": "system", "content": system},
                                        {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request((host or "http://localhost:11434") + "/api/chat", body,
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read())["message"]["content"]
    import anthropic
    client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(model=model, max_tokens=8000, system=system,
                                 messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


# ----------------------------------------------------------------------------------------------
# Parsing / normalising
# ----------------------------------------------------------------------------------------------
def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Model did not return JSON.")
    return json.loads(text[start: end + 1])


def _kw_list(v):
    out = []
    for k in v or []:
        if isinstance(k, str):
            out.append({"keyword": k, "aliases": []})
        elif isinstance(k, dict) and k.get("keyword"):
            out.append({"keyword": str(k["keyword"]), "aliases": [str(a) for a in k.get("aliases") or []]})
    return out


def _dict_list(v, keys):
    out = []
    for x in v or []:
        if isinstance(x, str):
            x = {keys[0]: x}
        if isinstance(x, dict):
            out.append({k: str(x.get(k, "") or "") for k in keys})
    return out


def normalise(data: dict, mode: str) -> dict:
    """Make the model output safe to use: fill missing keys, coerce shapes."""
    if not isinstance(data.get("resume"), dict) or not isinstance(data.get("jd"), dict):
        raise ValueError("JSON missing 'resume' or 'jd'.")
    jd = data["jd"]
    jd["must_have"], jd["nice_to_have"] = _kw_list(jd.get("must_have")), _kw_list(jd.get("nice_to_have"))
    for k in ("job_title", "seniority", "primary_stack"):
        jd[k] = str(jd.get(k) or "")
    for k in ("missing_unsupported", "top_fixes", "questions", "change_log"):
        data[k] = [str(x) for x in data.get(k) or []]
    data["unverified_claims"] = _dict_list(data.get("unverified_claims"), ["claim", "where", "risk"])
    data["stack_mapping"] = _dict_list(data.get("stack_mapping"),
                                       ["project", "original_stack", "presented_stack", "note"])
    data["interview_prep"] = _dict_list(data.get("interview_prep"), ["topic", "why", "prep"])
    data["cover_letter_opener"] = str(data.get("cover_letter_opener") or "")

    r = data["resume"]
    r["contact"] = r.get("contact") if isinstance(r.get("contact"), dict) else {}
    r["contact"]["links"] = [str(x) for x in r["contact"].get("links") or []]
    for k in ("skills", "experience", "projects", "education"):
        r[k] = [x for x in r.get(k) or [] if isinstance(x, dict)]
    for s in r["skills"]:
        s["items"] = [str(x) for x in s.get("items") or []]
    for e in r["experience"] + r["projects"]:
        e["bullets"] = [str(x) for x in e.get("bullets") or []]
    r["certifications"] = [str(x) for x in r.get("certifications") or []]
    data["mode"] = mode
    return data


def tailor(resume_text, jd_text, extra_notes, api_key, model, provider="Gemini", host=None, mode="honest") -> dict:
    system, user = build_system(mode), build_user(resume_text, jd_text, extra_notes, mode)
    last_err = None
    for _ in range(2):  # retry once on malformed output
        try:
            return normalise(_extract_json(_call(provider, system, user, api_key, model, host)), mode)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Could not get valid output from {provider}: {last_err}")
