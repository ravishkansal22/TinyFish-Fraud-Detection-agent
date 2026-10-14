"""
Is This Legit? -- verification checks built on TinyFish (Search, Fetch, Agent).

No Streamlit here, so every check can be run and tested from the command line:
    python checks.py "Zerodha"                       # full investigation
    python checks.py "Zerodha" https://zerodha.com --out demo/zerodha.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutTimeout
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------- env / clients
def load_env():
    """Load KEY=VALUE pairs from .env (never printed)."""
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

from tinyfish import AgentConfig, BrowserProfile, RunStatus, TinyFish  # noqa: E402

# Timeouts from the TinyFish docs' suggested values.
AGENT_TIMEOUT, FETCH_TIMEOUT, SEARCH_TIMEOUT = 120, 150, 10


def has_key() -> bool:
    return bool(os.environ.get("TINYFISH_API_KEY"))


# TinyFish queues Agent runs beyond the account's concurrency limit, and queue time counts against our
# 120s client timeout. Measured: 9 parallel runs all timed out, 1-2 runs finish in 35-60s. So we queue
# here instead, and the 120s timeout only covers the actual run.
AGENT_SLOTS = threading.Semaphore(int(os.environ.get("TINYFISH_AGENT_CONCURRENCY", "2")))


@lru_cache(maxsize=None)
def _client(timeout: int, retries: int) -> TinyFish:
    return TinyFish(timeout=timeout, max_retries=retries)


class Blocked(Exception):
    """Site showed a captcha / bot wall. We report it and never try to bypass it."""


BLOCK_SIGNALS = ("captcha", "verify you are human", "access denied", "are you a robot",
                 "unusual traffic", "cf-challenge", "attention required")


# ---------------------------------------------------------------- small helpers
def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def host_of(url: str) -> str:
    if not url:
        return ""
    host = urlparse(url if "://" in url else "https://" + url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


SECOND_LEVEL = {"co.in", "org.in", "net.in", "firm.in", "gen.in", "ind.in", "gov.in", "ac.in",
                "co.uk", "org.uk", "com.au", "com.sg"}


def registrable(host: str) -> str:
    """kite.zerodha.com -> zerodha.com ; app.foo.co.in -> foo.co.in"""
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def looks_blocked(text: str) -> bool:
    t = (text or "").lower()
    return any(s in t for s in BLOCK_SIGNALS)


# ---------------------------------------------------------------- TinyFish wrappers
def tf_search(query: str, **kw) -> list[dict]:
    """Search -> list of {url,title,snippet,site_name,date}. Raises on API error."""
    resp = _client(SEARCH_TIMEOUT, 1).search.query(query, location="IN", language="en", **kw)
    return [{"url": r.url, "title": r.title or r.url, "snippet": r.snippet or "",
             "site_name": r.site_name or host_of(r.url), "date": r.date} for r in resp.results]


def tf_fetch(url: str) -> dict:
    """Fetch one URL -> {url, final_url, title, text}. Raises on API/fetch error."""
    resp = _client(FETCH_TIMEOUT, 0).fetch.get_contents(urls=[url], format="markdown")
    if not resp.results:
        err = resp.errors[0].error if resp.errors else "no result"
        raise RuntimeError(f"fetch failed: {err}")
    r = resp.results[0]
    return {"url": r.url, "final_url": r.final_url or r.url, "title": r.title, "text": r.text or ""}


TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


def tf_cancel(run_id: str) -> None:
    """The SDK has no cancel method; this is POST /v1/runs/{id}/cancel from the TinyFish API reference."""
    try:
        httpx.post(f"https://agent.tinyfish.ai/v1/runs/{run_id}/cancel",
                   headers={"X-API-Key": os.environ["TINYFISH_API_KEY"]}, timeout=10)
    except Exception:  # noqa: BLE001
        pass


def tf_agent(url: str, goal: str, schema: dict, stealth: bool = False, max_seconds: int = 110) -> dict:
    """Agent run with a fixed output schema. Returns result dict; raises on failure / block / timeout.

    Uses agent.queue + runs.get polling rather than the blocking agent.run: sync runs cannot be cancelled,
    so a client-side timeout left orphaned runs that held the account's concurrency slots. Here a run that
    passes AGENT_TIMEOUT is cancelled on the server.
    """
    kw = {"browser_profile": BrowserProfile.STEALTH} if stealth else {}
    with AGENT_SLOTS:
        c = _client(30, 1)
        q = c.agent.queue(url=url, goal=goal, output_schema=schema,
                          agent_config=AgentConfig(max_duration_seconds=max_seconds), **kw)
        if not q.run_id:
            raise RuntimeError(f"agent queue failed: {q.error.message if q.error else 'no run id'}")
        deadline = time.time() + AGENT_TIMEOUT
        while True:
            time.sleep(3)
            r = c.runs.get(q.run_id)
            if r.status in TERMINAL:
                break
            if time.time() > deadline:
                tf_cancel(q.run_id)
                raise TimeoutError(f"agent run exceeded {AGENT_TIMEOUT}s (run {q.run_id[:8]} cancelled)")
    if r.status != RunStatus.COMPLETED:
        msg = r.error.message if r.error else str(r.status)
        if looks_blocked(msg):
            raise Blocked(msg)
        raise RuntimeError(f"agent run {r.status}: {msg}")
    res = dict(r.result or {})
    # COMPLETED does not mean success: the schema asks the agent to flag captchas / bot walls.
    if res.get("blocked_by_captcha"):
        raise Blocked("captcha / bot protection shown")
    res["_run_id"] = q.run_id
    return res


def safe(fn, *a, **kw) -> dict:
    """Run a check; never raise. Errors become a status the scorer understands."""
    t = time.time()
    try:
        out = fn(*a, **kw)
        out.setdefault("status", "ok")
    except Blocked as e:
        out = {"status": "blocked", "error": str(e)[:300]}
    except Exception as e:  # noqa: BLE001
        out = {"status": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"}
    out["seconds"] = round(time.time() - t, 1)
    return out


# ---------------------------------------------------------------- 1. find official site (Search)
AGGREGATORS = ("wikipedia.org", "play.google.com", "apps.apple.com", "linkedin.com", "facebook.com",
               "instagram.com", "youtube.com", "twitter.com", "x.com", "crunchbase.com", "glassdoor",
               "ambitionbox", "justdial", "quora.com", "reddit.com", "moneycontrol.com", "economictimes",
               "trustpilot.com", "scamadviser.com", "mouthshut.com")


def resolve_url(name: str) -> dict:
    hits = tf_search(f"{name} official website")
    key = norm(name)
    ok = [h for h in hits if h["url"] and not any(a in host_of(h["url"]) for a in AGGREGATORS)]
    # Prefer a domain that contains the name (zerodha -> zerodha.com).
    best = next((h for h in ok if key and key[:8] in norm(host_of(h["url"]))), None) or (ok[0] if ok else None)
    if not best:
        return {"status": "error", "error": "no official site found", "url": None}
    return {"url": best["url"], "source": best["url"], "title": best["title"]}


# ---------------------------------------------------------------- 2. inspect the site (Agent, stealth)
SITE_SCHEMA = {
    "type": "object",
    "properties": {
        "company_name": {"type": "string"},
        "claimed_registrations": {"type": "array", "items": {"type": "string"}},
        "registration_numbers": {"type": "array", "items": {"type": "string"}},
        "promised_returns": {"type": "string"},
        "guaranteed_returns": {"type": "boolean"},
        "upfront_payment_requested": {"type": "boolean"},
        "contact_channels": {"type": "array", "items": {
            "type": "string", "enum": ["whatsapp", "telegram", "phone", "email", "physical_address", "contact_form"]}},
        "red_flags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "blocked_by_captcha": {"type": "boolean"},
    },
    "required": ["company_name", "claimed_registrations", "guaranteed_returns",
                 "upfront_payment_requested", "contact_channels", "red_flags", "blocked_by_captcha"],
}


def inspect_site(url: str) -> dict:
    # Home page only: measured ~36s. Following About/Contact links timed out at 120s.
    goal = ("You are a fraud investigator. Read only this page (do not click anything, never sign up or submit "
            "forms). Fill the fields from what is visible: claimed registrations, registration numbers, any promised "
            "returns or daily income (exact wording), whether returns are guaranteed, whether an upfront fee or "
            "deposit is asked, contact channels, and up to 5 scam red flags (empty list if none). If a captcha or "
            "bot check blocks the page set blocked_by_captcha=true and stop; do not try to solve it.")
    res = tf_agent(url, goal, SITE_SCHEMA, stealth=True)
    res["source"] = url
    return res


# ---------------------------------------------------------------- 3. SEBI registry (Agent)
SEBI_BASE = "https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognisedFpi=yes&intmId={}"
SEBI_CATEGORIES = {30: "Stock Broker (equity)", 13: "Investment Adviser"}
SEBI_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {"type": "array", "maxItems": 10, "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "registration_no": {"type": "string"}},
            "required": ["name", "registration_no"]}},
        "no_records": {"type": "boolean"},
        "blocked_by_captcha": {"type": "boolean"},
    },
    "required": ["matches", "no_records", "blocked_by_captcha"],
}


def _sebi_query_term(name: str) -> str:
    """SEBI's search is a substring match; the first distinctive word works best."""
    stop = {"the", "pvt", "private", "ltd", "limited", "india", "app", "capital", "securities", "broking"}
    ws = [w for w in words(name) if w not in stop and len(w) >= 3]
    return ws[0] if ws else name


def _name_matches(query: str, candidate: str) -> bool:
    q = [w for w in words(query) if len(w) >= 3]
    c = set(words(candidate))
    return bool(q) and all(w in c for w in q[:2])


def _sebi_category(intm_id: int, term: str) -> dict:
    url = SEBI_BASE.format(intm_id)
    # Short, literal goal: measured ~60s. A longer goal timed out at 120s.
    goal = (f"Type '{term}' into the Name text box and press Enter or click GO. Then read the results list and "
            "return each entry's name and registration number (max 10). If it says no records, return an empty "
            "list with no_records=true. If a captcha appears, set blocked_by_captcha=true and stop.")
    res = tf_agent(url, goal, SEBI_SCHEMA, max_seconds=110)
    res["page_url"] = url
    return res


def sebi_check(name: str) -> dict:
    """Stock Brokers first; Investment Advisers only if the broker search finds nothing.
    Sequential on purpose: it keeps each case at 2 concurrent Agent runs (site + SEBI)."""
    term = _sebi_query_term(name)
    matches, searched, failed, per = [], [], [], {}
    for cid, label in SEBI_CATEGORIES.items():
        r = per[cid] = safe(_sebi_category, cid, term)
        if r["status"] != "ok":
            failed.append({"category": label, "status": r["status"], "error": r.get("error")})
            continue
        searched.append(label)
        matches += [{**m, "category": label, "page_url": r["page_url"]}
                    for m in r.get("matches") or [] if _name_matches(name, m.get("name", ""))]
        if matches:
            break
    base = {"term": term, "searched": searched, "failed": failed, "per_category": per, "matches": matches,
            "found": bool(matches), "page_url": matches[0]["page_url"] if matches else SEBI_BASE.format(30)}
    if matches:
        return base
    if not searched:
        status = "blocked" if any(f["status"] == "blocked" for f in failed) else "error"
        return {**base, "status": status, "error": "; ".join(f"{f['category']}: {f['error']}" for f in failed)}
    if failed:  # some categories could not be searched: absence is not proven
        return {**base, "status": "partial", "error": "; ".join(f"{f['category']}: {f['error']}" for f in failed)}
    return base


# ---------------------------------------------------------------- 4. RBI Alert List (Search + Fetch)
RBI_ALERT_FALLBACK = "https://rbi.org.in/scripts/bs_viewcontent.aspx?Id=4235"
# Words too common in forex-platform names to count as a match on their own.
COMMON = {"forex", "fx", "trade", "trading", "markets", "market", "capital", "global", "invest", "investment",
          "option", "options", "pro", "money", "finance", "financial", "india", "app", "online", "com", "www",
          "limited", "ltd", "pvt", "group", "securities", "broker", "brokers", "plus", "one", "the"}


def parse_alert_list(text: str) -> list[dict]:
    """Rows look like: | 12 | Foxorex | https://foxorex.com |"""
    rows = []
    for m in re.finditer(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|", text, re.M):
        sites = re.findall(r"https?://[^\s|,;<>]+", m.group(3))
        rows.append({"sr": int(m.group(1)), "name": m.group(2).strip(),
                     "domains": [registrable(host_of(s)) for s in sites]})
    return rows


def alert_match(name: str, domain: str, rows: list[dict]) -> list[dict]:
    qn, qw = norm(name), words(name)
    q_phrase = " " + " ".join(qw) + " "
    dom = registrable(domain) if domain else ""
    hits = []
    for r in rows:
        en, ew = norm(r["name"]), words(r["name"])
        e_phrase = " " + " ".join(ew) + " "
        why = None
        if dom and dom in r["domains"]:
            why = f"domain {dom} is listed"
        elif qn and qn == en:
            why = "exact name match"
        elif len(ew) >= 2 and len(en) >= 6 and e_phrase in q_phrase and not set(ew) <= COMMON:
            why = "listed name appears in your query"     # "IQ Option app" contains "iq option"
        elif len(qn) >= 6 and q_phrase in e_phrase and not set(qw) <= COMMON:
            why = "your query is part of the listed name"  # "Olymp" vs "Olymp Trade" is <6, skipped
        if why:
            hits.append({**r, "why": why})
    return hits


def rbi_alert_check(name: str, domain: str) -> dict:
    page, how = None, "search"
    try:
        hits = tf_search("RBI Alert List unauthorised forex trading platforms", include_domains="rbi.org.in")
        page = next((h["url"] for h in hits if "bs_viewcontent" in h["url"].lower()), None)
    except Exception:  # noqa: BLE001
        pass
    if not page:
        page, how = RBI_ALERT_FALLBACK, "fallback URL"
    doc = tf_fetch(page)
    rows = parse_alert_list(doc["text"])
    if "alert list" not in doc["text"].lower() or len(rows) < 20:
        return {"status": "error", "error": f"Alert List page did not parse ({len(rows)} rows)",
                "listed": False, "page_url": page}
    hits = alert_match(name, domain, rows)
    return {"listed": bool(hits), "matches": hits, "entries": len(rows), "page_url": page, "found_via": how}


# ---------------------------------------------------------------- 5. WHOIS domain age (Fetch)
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_LABEL = (r"(?:Registered On|Creation Date|Created On|Created Date|Created|Registration Date|"
              r"Registration Time|Domain Registration Date)")


def parse_created(text: str) -> dt.date | None:
    m = re.search(r"eventAction\W+registration\W+eventDate\W+(\d{4})-(\d{2})-(\d{2})", text)  # RDAP JSON
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(DATE_LABEL + r"\s*:?[\s*|]*(\d{4})-(\d{2})-(\d{2})", text, re.I)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(DATE_LABEL + r"\s*:?[\s*|]*(\d{1,2})[-/ ]([A-Za-z]{3})[a-z]*[-/ ](\d{4})", text, re.I)
    if m and m.group(2).lower() in MONTHS:
        return dt.date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    return None


WHOIS_SOURCES = ("https://www.whois.com/whois/{}", "https://rdap.org/domain/{}")


def domain_age(domain: str) -> dict:
    if not domain:
        return {"status": "error", "error": "no domain to check", "days": None}
    dom = registrable(domain)
    errors = []
    for tpl in WHOIS_SOURCES:
        src = tpl.format(dom)
        try:
            text = tf_fetch(src)["text"]
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src}: {e}")
            continue
        created = parse_created(text)
        if created:
            return {"domain": dom, "days": (dt.date.today() - created).days, "created": str(created), "source": src}
        blocked = looks_blocked(text) or "security check" in text.lower()
        errors.append(f"{src}: {'blocked by security check' if blocked else 'no creation date in record'}")
    return {"status": "error", "error": "; ".join(errors), "domain": dom, "days": None,
            "source": WHOIS_SOURCES[0].format(dom)}


# ---------------------------------------------------------------- 6. fraud / complaint reports (Search)
SCAM_WORDS = ("scam", "fraud", "fake", "cheat", "ponzi", "unauthorised", "unauthorized", "lost money",
              "duped", "arrested", "complaint", "warning", "alert list", "illegal")


# Articles about scammers impersonating a brand are not reports against the brand itself.
IMPERSONATION = ("warns", "pretending to be", "impersonat", "posing as", "in the name of", "how to verify",
                 "stay safe", "beware of fake")


def complaints(name: str, official_domain: str = "") -> dict:
    seen, out = set(), []
    key = norm(name)
    queries = ((f"{name} scam fraud complaint", {}), (f"{name} fraud", {"domain_type": "news"}))
    for q, kw in queries:
        for h in tf_search(q, **kw):
            blob = f"{h['title']} {h['snippet']}"
            if not h["url"] or h["url"] in seen:
                continue
            if official_domain and registrable(host_of(h["url"])) == registrable(official_domain):
                continue  # the company's own fraud-awareness page is not a report against it
            low = blob.lower()
            if key in norm(blob) and any(w in low for w in SCAM_WORDS) and not any(w in low for w in IMPERSONATION):
                seen.add(h["url"])
                out.append({"title": h["title"], "url": h["url"], "snippet": h["snippet"][:240],
                            "site": h["site_name"], "date": h.get("date")})
    return {"reports": out[:8]}


# ---------------------------------------------------------------- scoring
def score(site: dict, sebi: dict, rbi: dict, age: dict, rep: dict) -> tuple[int, str, list[dict]]:
    pts, ev = 0, []

    def add(p, sev, check, text, link=None):
        nonlocal pts
        pts += p
        ev.append({"severity": sev, "check": check, "finding": text, "points": p, "source": link or ""})

    def unverified(check, r, link):
        why = {"blocked": "blocked by captcha / bot protection",
               "partial": f"only searched {', '.join(r.get('searched', []))}; other categories timed out"
               }.get(r.get("status"), "check failed or timed out")
        add(5, "🟡", check, f"Could not verify ({why})", link)

    # RBI Alert List
    if rbi.get("status", "ok") != "ok":
        unverified("RBI Alert List", rbi, rbi.get("page_url"))
    elif rbi.get("listed"):
        m = rbi["matches"][0]
        add(70, "🔴", "RBI Alert List",
            f"On RBI Alert List of unauthorised forex platforms: '{m['name']}' ({m['why']})", rbi["page_url"])
    else:
        add(0, "🟢", "RBI Alert List", f"Not on RBI Alert List ({rbi.get('entries')} entries checked)", rbi["page_url"])

    # SEBI registry
    claims_sebi = any("sebi" in c.lower() for c in (site.get("claimed_registrations") or []))
    if sebi.get("status", "ok") != "ok":
        unverified("SEBI registry", sebi, sebi.get("page_url"))
    elif sebi.get("found"):
        m = sebi["matches"][0]
        add(-40, "🟢", "SEBI registry",
            f"SEBI registered {m['category']}: {m['name']} ({m['registration_no']})", m["page_url"])
    elif claims_sebi:
        add(45, "🔴", "SEBI registry", "Site claims SEBI registration but no match in SEBI's register",
            sebi.get("page_url"))
    else:
        add(15, "🟡", "SEBI registry",
            f"Not found among SEBI-registered {' / '.join(sebi.get('searched', []))}s", sebi.get("page_url"))

    # Site inspection
    src = site.get("source") or site.get("url")
    if site.get("status", "ok") != "ok":
        unverified("Site inspection", site, src)
    else:
        before = len(ev)
        if site.get("guaranteed_returns"):
            add(25, "🔴", "Site inspection",
                f"Promises fixed/guaranteed returns: {site.get('promised_returns') or 'yes'}", src)
        if site.get("upfront_payment_requested"):
            add(15, "🔴", "Site inspection", "Asks for an upfront deposit / registration fee", src)
        ch = [c.lower() for c in site.get("contact_channels") or []]
        if ch and all(c in ("whatsapp", "telegram") for c in ch):
            add(15, "🔴", "Site inspection", "Only WhatsApp/Telegram contact, no address or email", src)
        for f in (site.get("red_flags") or [])[:3]:
            add(5, "🟡", "Site inspection", f"Red flag: {f}", src)
        if len(ev) == before:
            add(0, "🟢", "Site inspection", "No guaranteed returns, upfront fees or chat-only contact found", src)

    # Domain age
    d = age.get("days")
    if d is None:
        unverified("Domain age (WHOIS)", age, age.get("source"))
    elif d < 90:
        add(30, "🔴", "Domain age (WHOIS)", f"Domain registered only {d} days ago ({age['created']})", age["source"])
    elif d < 365:
        add(15, "🟡", "Domain age (WHOIS)", f"Domain less than a year old: {d} days ({age['created']})", age["source"])
    else:
        add(0, "🟢", "Domain age (WHOIS)", f"Domain is {d // 365} years old (registered {age['created']})",
            age["source"])

    # Reports online
    if rep.get("status", "ok") != "ok":
        unverified("Fraud reports", rep, None)
    else:
        n = len(rep.get("reports") or [])
        # A registered entity attracts "X warns of fraud" articles, so reports weigh less once SEBI confirms it.
        per = 3 if sebi.get("found") else 8
        if n:
            add(min(25, per * n), "🔴" if n >= 3 and not sebi.get("found") else "🟡", "Fraud reports",
                f"{n} fraud/complaint mentions found online", rep["reports"][0]["url"])
        else:
            add(0, "🟢", "Fraud reports", "No fraud or complaint reports found")

    pts = max(0, min(100, pts))
    verdict = "🔴 LIKELY SCAM" if pts >= 60 else "🟡 UNVERIFIED: be careful" if pts >= 25 else "🟢 LOOKS LEGIT"
    return pts, verdict, ev


# ---------------------------------------------------------------- pipeline
CHECK_LABELS = {"site": "Site inspection (Agent, stealth browser)", "sebi": "SEBI registry lookup (Agent)",
                "rbi": "RBI Alert List (Search + Fetch)", "whois": "Domain age (Fetch WHOIS)",
                "reports": "Fraud reports (Search web + news)"}
DEADLINE = 200  # seconds for the parallel phase; anything slower is marked "could not verify"


def investigate(name: str, url: str | None = None, progress=None) -> dict:
    """progress(states) is called from the calling thread with {check: 'running'|'ok'|'error'|'blocked'}."""
    t0 = time.time()
    states = {k: "waiting" for k in CHECK_LABELS}

    def note():
        if progress:
            progress(dict(states))

    resolved = None
    if not url:
        states["site"] = "finding website"
        note()
        resolved = safe(resolve_url, name)
        url = resolved.get("url")
    domain = host_of(url) if url else ""

    ex = ThreadPoolExecutor(max_workers=5)
    jobs = {"sebi": (sebi_check, name), "rbi": (rbi_alert_check, name, domain),
            "whois": (domain_age, domain), "reports": (complaints, name, domain)}
    if url:
        jobs["site"] = (inspect_site, url)
    futs = {ex.submit(safe, *job): k for k, job in jobs.items()}
    results = {"site": {"status": "error", "error": "no website found"}}
    for k in jobs:
        states[k] = "running"
    if not url:
        states["site"] = "error"
    note()
    try:
        for f in as_completed(futs, timeout=DEADLINE):
            k = futs[f]
            results[k] = f.result()
            states[k] = results[k]["status"]
            note()
    except FutTimeout:
        for k in jobs:
            if k not in results:
                results[k] = {"status": "error", "error": f"timed out after {DEADLINE}s"}
                states[k] = "error"
        note()
    ex.shutdown(wait=False, cancel_futures=True)

    pts, verdict, ev = score(results["site"], results["sebi"], results["rbi"], results["whois"], results["reports"])
    return {"name": name, "url": url, "url_found_by_search": bool(resolved), "risk": pts, "verdict": verdict,
            "evidence": ev, "reports": results["reports"].get("reports", []),
            "seconds": round(time.time() - t0, 1), "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
            "raw": {"resolve": resolved, **results}}


if __name__ == "__main__":
    import argparse
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("url", nargs="?")
    ap.add_argument("--out")
    a = ap.parse_args()
    if not has_key():
        sys.exit("TINYFISH_API_KEY not set (env or .env)")
    res = investigate(a.name, a.url, progress=lambda s: print("  ", s, flush=True))
    print(json.dumps({k: res[k] for k in ("name", "url", "risk", "verdict", "seconds")}, ensure_ascii=False))
    for e in res["evidence"]:
        print(f"  {e['severity']} [{e['points']:+}] {e['check']}: {e['finding']}  <{e['source']}>")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
