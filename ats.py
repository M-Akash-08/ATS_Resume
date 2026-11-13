"""Local, deterministic ATS-style scoring (no LLM). Estimates only - real ATS vendors differ."""
import re


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9+#.\s/&-]", " ", s)
    return re.sub(r"\s+", " ", s)


def _has(text_norm: str, term: str) -> bool:
    t = _norm(term).strip()
    if not t:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text_norm) is not None


def keyword_hits(text: str, keywords: list[dict]):
    """keywords: [{'keyword': str, 'aliases': [str]}]. Returns (matched, missing)."""
    tn = _norm(text)
    matched, missing = [], []
    for k in keywords:
        terms = [k["keyword"]] + list(k.get("aliases", []))
        (matched if any(_has(tn, t) for t in terms) else missing).append(k["keyword"])
    return matched, missing


def resume_to_text(r: dict) -> str:
    parts = [r.get("name", ""), r.get("headline", ""), r.get("summary", "")]
    c = r.get("contact", {})
    parts += [c.get("email", ""), c.get("phone", ""), c.get("location", "")] + list(c.get("links", []))
    parts.append("SKILLS")
    for s in r.get("skills", []):
        parts.append(s.get("category", "") + ": " + ", ".join(s.get("items", [])))
    parts.append("EXPERIENCE")
    for e in r.get("experience", []):
        parts.append(f'{e.get("title","")} {e.get("company","")} {e.get("dates","")}')
        parts += ["- " + b for b in e.get("bullets", [])]
    if r.get("projects"):
        parts.append("PROJECTS")
        for p in r["projects"]:
            parts.append(p.get("name", "") + " " + " ".join(p.get("bullets", [])))
    parts.append("EDUCATION")
    for e in r.get("education", []):
        parts.append(f'{e.get("degree","")} {e.get("school","")} {e.get("dates","")} {e.get("details","")}')
    if r.get("certifications"):
        parts.append("CERTIFICATIONS")
        parts += r["certifications"]
    return "\n".join(p for p in parts if p)


def format_score(text: str) -> tuple[float, list[str]]:
    """0-1 score for parseability/completeness heuristics + list of issues."""
    issues, pts = [], 0.0
    tl = text.lower()
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text): pts += 0.2
    else: issues.append("No email found")
    if re.search(r"\+?\d[\d\s-]{8,}", text): pts += 0.1
    else: issues.append("No phone number found")
    for sec, w in (("experience", 0.15), ("education", 0.1), ("skills", 0.15)):
        if sec in tl: pts += w
        else: issues.append(f"No '{sec.title()}' heading found")
    lines = [l for l in text.splitlines() if len(l.strip()) > 40]
    if lines:
        quant = sum(1 for l in lines if re.search(r"\d", l)) / len(lines)
        pts += 0.3 * min(1.0, quant / 0.5)
        if quant < 0.4: issues.append("Few bullets contain numbers/metrics")
    return min(pts, 1.0), issues


def score(text: str, must: list[dict], nice: list[dict]) -> dict:
    mm, mx = keyword_hits(text, must)
    nm, nx = keyword_hits(text, nice)
    must_cov = len(mm) / len(must) if must else 1.0
    nice_cov = len(nm) / len(nice) if nice else 1.0
    fmt, issues = format_score(text)
    total = 100 * (0.60 * must_cov + 0.15 * nice_cov + 0.25 * fmt)
    return {
        "total": round(total),
        "must_cov": round(100 * must_cov), "nice_cov": round(100 * nice_cov), "format": round(100 * fmt),
        "must_matched": mm, "must_missing": mx, "nice_matched": nm, "nice_missing": nx, "issues": issues,
    }
