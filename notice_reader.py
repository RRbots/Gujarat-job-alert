"""Notice (web page ya PDF) poora padhke eligibility ki jaankari nikalta hai.

read_notice(url)  -> notice ka poora text (page ho to usme jude advertisement PDF bhi)
analyze(text, level_patterns, grad_patterns) -> dict: qualification verdict, last date, posts, age, CCC...
"""
import io
import re
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

try:
    from pypdf import PdfReader
except ImportError:  # pypdf na ho to PDF skip hogi, baaki chalega
    PdfReader = None

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GujaratJobAlert/1.0)", "Accept-Language": "en,gu;q=0.8"}
MAX_PDF_PAGES = 12
MAX_BYTES = 15 * 1024 * 1024
MAX_PDFS_PER_PAGE = 2
PDF_HINT = re.compile(r"advert|recruit|vacanc|notif|bharti|ભરતી|જાહેરાત|adv", re.I)

_GU_DIGITS = str.maketrans("૦૧૨૩૪૫૬૭૮૯", "0123456789")


def _get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=40)
    except requests.exceptions.SSLError:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = requests.get(url, headers=HEADERS, timeout=40, verify=False)
    r.raise_for_status()
    if len(r.content) > MAX_BYTES:
        raise ValueError("file bahut badi hai")
    return r


def _is_pdf(url, r):
    return ("pdf" in r.headers.get("content-type", "").lower()
            or url.lower().split("?")[0].endswith(".pdf")
            or r.content[:5] == b"%PDF-")


def pdf_text(content):
    if PdfReader is None:
        return ""
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            reader.decrypt("")
        parts = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                pass
        return "\n".join(parts)
    except Exception:
        return ""


def read_notice(url):
    """Notice ka text lautata hai. Network error aaye to exception uthta hai."""
    r = _get(url)
    if _is_pdf(url, r):
        return pdf_text(r.content)

    soup = BeautifulSoup(r.content, "html.parser")
    pdf_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        label = a.get_text(" ", strip=True)
        if href.lower().split("?")[0].endswith(".pdf") and (PDF_HINT.search(label) or PDF_HINT.search(href)):
            full = urljoin(url, href)
            if full not in pdf_links:
                pdf_links.append(full)
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)

    for pdf in pdf_links[:MAX_PDFS_PER_PAGE]:
        try:
            pr = _get(pdf)
            text += "\n" + pdf_text(pr.content)
        except Exception:
            continue
    return text


QUAL_RE = re.compile(
    r"(educational\s+qualifications?|essential\s+qualifications?|qualifications?|શૈક્ષણિક\s*લાયકાત|લાયકાત)",
    re.I)
DATE = r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?[\s-]+[A-Za-z]{3,9}[\s,.-]+\d{4})"
LASTDATE_RE = re.compile(
    r"(?:last\s*date|closing\s*date|છેલ્લી\s*તારીખ|અંતિમ\s*તારીખ)[^0-9]{0,80}?" + DATE, re.I)
VAC_RE = re.compile(
    r"(?:total\s*(?:no\.?\s*of\s*)?(?:posts?|vacanc(?:y|ies))|no\.?\s*of\s*posts?|કુલ\s*જગ્યા(?:ઓ)?)"
    r"[^0-9]{0,40}(\d{1,5})", re.I)
AGE_RE = re.compile(r"(?:age\s*limit|maximum\s*age|upper\s*age|વય\s*મર્યાદા|ઉંમર)(.{0,140})", re.I)
CCC_RE = re.compile(r"\bccc\b|computer\s*(?:knowledge|proficiency|course|literacy)|કોમ્પ્યુટર", re.I)
SCIENCE_RE = re.compile(r"\bscience\b|\bpcm\b|વિજ્ઞાન", re.I)


STOP_QUAL = re.compile(
    r"age\s*limit|maximum\s*age|upper\s*age|last\s*date|closing\s*date|pay\s*scale|fixed\s*pay|salary"
    r"|selection\s*process|how\s*to\s*apply|application\s*fee"
    r"|વય\s*મર્યાદા|ઉંમર|છેલ્લી\s*તારીખ|અંતિમ\s*તારીખ|પગાર", re.I)
STOP_AGE = re.compile(
    r"last\s*date|closing\s*date|pay\s*scale|fixed\s*pay|salary|selection|application\s*fee"
    r"|છેલ્લી\s*તારીખ|અંતિમ\s*તારીખ|પગાર", re.I)


def _cut(text, stop):
    m = stop.search(text)
    return text[: m.start()] if m else text


def _any(patterns, text):
    return any(re.search(p, text, re.I) for p in patterns)


def analyze(raw_text, level_patterns, grad_patterns):
    flat = re.sub(r"\s+", " ", raw_text or "").translate(_GU_DIGITS)[:300000]
    info = {"chars": len(flat), "readable": len(flat) >= 200}
    if not info["readable"]:
        info["verdict"] = "unreadable"
        return info

    snippets = []
    for m in QUAL_RE.finditer(flat):
        snippets.append(_cut(flat[m.end(): m.end() + 320], STOP_QUAL))
        if len(snippets) >= 6:
            break
    qual = " ".join(snippets)
    has_lvl, has_grad = _any(level_patterns, qual), _any(grad_patterns, qual)
    if has_lvl and not has_grad:
        info["verdict"] = "eligible"
    elif has_grad and not has_lvl:
        info["verdict"] = "graduate"
    elif has_lvl and has_grad:
        info["verdict"] = "mixed"
    else:
        info["verdict"] = "unknown"
    first = next((s for s in snippets if s.strip(" :-–.")), "")
    info["qual"] = first.strip(" :-–.")[:180]
    info["science"] = bool(SCIENCE_RE.search(qual))

    m = LASTDATE_RE.search(flat)
    info["last_date"] = m.group(1) if m else ""
    m = VAC_RE.search(flat)
    info["posts"] = m.group(1) if m else ""
    m = AGE_RE.search(flat)
    info["age"] = _cut(m.group(1), STOP_AGE).strip(" :-–.")[:110] if m else ""
    info["ccc"] = bool(CCC_RE.search(flat))
    return info
