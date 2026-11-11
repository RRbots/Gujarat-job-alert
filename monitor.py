"""Gujarat govt job alert -> Telegram.

10th/12th pass bharti + PCM/Computer/Police/Clerk type posts ka alert deta hai.
Graduate wali bharti skip hoti hai.

Use:
  python monitor.py            # normal run (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID chahiye)
  python monitor.py --test     # sirf ek test message bhejta hai
  python monitor.py --dry-run  # Telegram bheje bina matches print karta hai
"""
import hashlib
import json
import os
import re
import sys
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

import notice_reader

# name: (url, trusted)
# trusted=True: board ki official site. Yahan har nayi "bharti/advertisement" ka alert aata hai,
# chahe title me qualification na likhi ho (tag: "Qualification check karo").
SOURCES = {
    "OJAS": ("https://ojas.gujarat.gov.in/", True),
    "GSSSB": ("https://gsssb.gujarat.gov.in/", True),
    "GPSSB": ("https://gpssb.gujarat.gov.in/notifications.htm", True),
    "GPRB": ("https://gprb.gujarat.gov.in/", True),
    "Ahmedabad MC": ("https://ahmedabadcity.gov.in/", False),
    "Surat MC": ("https://www.suratmunicipal.gov.in/", False),
    "Vadodara MC": ("https://vmc.gov.in/", False),
    "Rajkot MC": ("https://www.rmc.gov.in/", False),
    "Bhavnagar MC": ("https://bmcgujarat.com/", False),
    "Jamnagar MC": ("https://mcjamnagar.com/", False),
    "Junagadh MC": ("https://jumc.gujarat.gov.in/", False),
    "Gandhinagar MC": ("https://gandhinagarmunicipal.com/", False),  # URL verify nahi hua
    # Backup: Gujarat job news blogs (jo bharti official site se chhoot jaye)
    "MaruGujarat": ("https://www.marugujarat.in/", False),
    "Rijadeja": ("https://updates.rijadeja.com/", False),
}

NUM = r"(?<![0-9૦-૯])"
LEVEL = [
    r"\b12[\s-]*th\b", r"\b12[\s-]*pass", r"\bhsc\b", r"higher secondary",
    r"ધોરણ\s*-?\s*(12|૧૨)(?![0-9૦-૯])", NUM + r"૧૨\s*પાસ",
    r"\b10[\s-]*th\b", r"\b10[\s-]*pass", r"\bssc[\s-]*pass", r"\bsslc\b",
    r"ધોરણ\s*-?\s*(10|૧૦)(?![0-9૦-૯])", NUM + r"૧૦\s*પાસ",
]
RELATED = [
    r"computer", r"data\s*entry", r"\bccc\b", r"junior clerk", r"jr\.?\s*clerk", r"\bclerk\b",
    r"office assistant", r"technical assistant", r"lab assistant", r"surveyor", r"operator",
    r"\bpcm\b", r"constable", r"lokrakshak", r"\blrd\b", r"\bsrp\b", r"police", r"sepoy",
    r"jail\s*sipahi", r"fire\s*man", r"fireman", r"forest guard", r"vanrakshak", r"talati",
    r"peon", r"driver", r"conductor",
    r"કોમ્પ્યુટર", r"ક્લાર્ક", r"ઓપરેટર", r"પોલીસ", r"કોન્સ્ટેબલ", r"લોકરક્ષક", r"તલાટી",
    r"ફાયરમેન", r"ડ્રાઇવર", r"કંડક્ટર", r"પટાવાળા", r"સિપાહી",
]
RECRUIT = [
    r"recruit", r"vacanc", r"advertis", r"notification", r"bharti", r"apply\s+online",
    r"ભરતી", r"જાહેરાત",
]
RECRUIT_STRICT = [r"recruit", r"vacanc", r"advertis", r"bharti", r"ભરતી", r"જાહેરાત"]
# Graduate/degree wali bharti hatao
EXCLUDE_HARD = [
    r"graduat", r"post[\s-]*graduate", r"degree", r"\bb\.e\b", r"\bb\.?\s*tech\b", r"\bb\.?\s*sc\b",
    r"\bb\.?\s*com\b", r"\bm\.?\s*sc\b", r"\bm\.?\s*tech\b", r"\bmba\b", r"\bmca\b", r"\bllb\b",
    r"સ્નાતક",
]
# Sirf "qualification check karo" wale tier ke liye: badi posts hatao
EXCLUDE_POST = [
    r"engineer", r"professor", r"medical officer", r"doctor", r"physician", r"specialist",
    r"consultant", r"geolog", r"scientific", r"\bofficer\b", r"inspector", r"lecturer",
    r"gynaec", r"paediatric", r"pediatric", r"anaesthe", r"radiolog",
]
# Result/call letter jaisi cheezein nayi bharti nahi hoti
NOISE = [
    r"result", r"merit", r"call[\s-]*letter", r"hall[\s-]*ticket", r"answer[\s-]*key",
    r"admit[\s-]*card", r"cut[\s-]*off", r"પરિણામ", r"કોલ\s*લેટર", r"આન્સર\s*કી",
]

SEEN_FILE = "seen.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GujaratJobAlert/1.0)", "Accept-Language": "en,gu;q=0.8"}
MAX_PER_RUN = 25
DIGEST_MAX = 15


def match(patterns, text):
    return any(re.search(p, text, re.I) for p in patterns)


def classify(text, trusted):
    if match(NOISE, text):
        return None
    lvl, rel, rec = match(LEVEL, text), match(RELATED, text), match(RECRUIT, text)
    if lvl and (rec or rel):
        return "10th/12th pass"
    hard = match(EXCLUDE_HARD, text)
    if rel and rec and not hard:
        return "PCM/Computer/Police type"
    if trusted and match(RECRUIT_STRICT, text) and not hard and not match(EXCLUDE_POST, text):
        return "Qualification check karo"
    return None


def http_get(url):
    try:
        return requests.get(url, headers=HEADERS, timeout=30)
    except requests.exceptions.SSLError:
        # Kai sarkari sites ka certificate kharab hota hai; sirf public page padh rahe hain
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return requests.get(url, headers=HEADERS, timeout=30, verify=False)


def fetch_links(url):
    r = http_get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        text = " ".join(a.get_text(" ", strip=True).split())
        if len(text) < 6:
            continue
        out.append((text[:300], urljoin(url, href)))
    return out


def send(msg):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID set nahi hai")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": msg, "disable_web_page_preview": True},
            timeout=30,
        )
        if r.ok and r.json().get("ok"):
            return True
        print("Telegram error:", r.status_code, r.text[:200])
    except Exception as e:  # token leak na ho isliye sirf type print karte hain
        print("Telegram request fail:", type(e).__name__)
    return False


def load_state():
    if os.path.exists(SEEN_FILE):
        try:
            data = json.load(open(SEEN_FILE))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"initialized": False, "seen": []}


def save_state(state):
    json.dump(state, open(SEEN_FILE, "w"), indent=1)


VERDICT_LINE = {
    "eligible": "✅ Notice me 10th/12th level ki yogyata mili",
    "graduate": "⚠️ Notice me graduate level dikh raha hai, title se alag. Check karo",
    "mixed": "⚠️ Kuch posts graduate wali bhi hain. Apni post ki shart check karo",
    "unknown": "❓ Notice me qualification saaf nahi mili. Khud check karo",
    "unreadable": "❓ Notice padha nahi ja saka (scan PDF ya khali page). Khud check karo",
}


def fmt(item, info=None):
    name, tag, text, link = item
    lines = [f"🆕 {name} [{tag}]", text]
    if info is None:
        lines.append("❓ Notice khul nahi paya. Khud check karo")
    else:
        lines.append(VERDICT_LINE[info["verdict"]])
        if info.get("qual"):
            lines.append("🎓 " + info["qual"])
        if info.get("science"):
            lines.append("🔬 Science stream ka zikr hai")
        if info.get("posts"):
            lines.append(f"👥 Total posts: {info['posts']}")
        if info.get("last_date"):
            lines.append(f"📅 Last date: {info['last_date']}")
        if info.get("age"):
            lines.append("🎂 " + info["age"])
        if info.get("ccc"):
            lines.append("💻 CCC/computer knowledge ka zikr hai")
    lines.append(link)
    return "\n".join(lines)


def prepare(item):
    """Notice poora padhke message banata hai. None matlab skip (sirf graduate wali bharti)."""
    name, tag, text, link = item
    info = None
    try:
        info = notice_reader.analyze(notice_reader.read_notice(link), LEVEL, EXCLUDE_HARD)
    except Exception as e:
        print(f"[read fail] {name}: {type(e).__name__}")
    if info and info["verdict"] == "graduate" and tag != "10th/12th pass":
        print(f"[skip graduate-only] {text[:80]}")
        return None
    return fmt(item, info)


def main():
    args = sys.argv[1:]
    if "--test" in args:
        ok = send("✅ Test message: Gujarat Job Alert bot Telegram se connected hai.")
        print("Test message bheja" if ok else "Test message fail")
        sys.exit(0 if ok else 1)
    dry = "--dry-run" in args

    state = load_state()
    seen = set(state["seen"])
    first_run = not state.get("initialized")

    found, keys, ok_sources = [], [], 0
    for name, (url, trusted) in SOURCES.items():
        try:
            links = fetch_links(url)
        except Exception as e:
            print(f"[skip] {name}: {type(e).__name__}")
            continue
        ok_sources += 1
        local = set()
        for text, link in links:
            tag = classify(text, trusted)
            if not tag:
                continue
            # link nahi, sirf site+title: kuch sites link me session id badalti hain
            key = hashlib.md5((name + "|" + text.lower()).encode()).hexdigest()
            if key in seen or key in local:
                continue
            local.add(key)
            found.append((name, tag, text, link))
            keys.append(key)
    print(f"{ok_sources}/{len(SOURCES)} sites khuli, {len(found)} naye match")

    if dry:
        for item in found:
            msg = prepare(item)
            if msg:
                print(msg, "\n")
        return
    if ok_sources == 0:
        print("Koi bhi site nahi khuli, is run ko fail mark kar raha hu")
        sys.exit(1)

    if first_run:
        if not send(f"✅ Gujarat Job Alert chalu ho gaya ({ok_sources}/{len(SOURCES)} sites check hui). "
                    "Ab naya notice aate hi yahan aayega."):
            print("Telegram setup galat hai (token/chat id check karo), state save nahi kiya")
            sys.exit(1)
        if found:
            lines = [m for m in (prepare(i) for i in found[:DIGEST_MAX]) if m]
            digest, size = [], 0
            for ln in lines:
                if size + len(ln) > 3500:
                    send("📋 Abhi chal rahi bharti:\n\n" + "\n\n".join(digest))
                    digest, size = [], 0
                digest.append(ln)
                size += len(ln) + 2
            if digest:
                send("📋 Abhi chal rahi bharti:\n\n" + "\n\n".join(digest))
        state = {"initialized": True, "seen": sorted(seen | set(keys))}
        save_state(state)
        return

    sent_keys, sent_count = [], 0
    for item, key in zip(found, keys):
        if sent_count >= MAX_PER_RUN:
            break
        msg = prepare(item)
        if msg is None:  # graduate-only: alert nahi, par dobara bhi nahi dekhna
            sent_keys.append(key)
            continue
        if not send(msg):
            break  # fail hua to baaki agli run me dobara try honge
        sent_keys.append(key)
        sent_count += 1
    # Cap se bache hue alerts seen me nahi jaate, agli run me bheje jayenge
    state = {"initialized": True, "seen": sorted(seen | set(sent_keys))}
    save_state(state)
    print(f"{sent_count} alert bheje")


if __name__ == "__main__":
    main()
