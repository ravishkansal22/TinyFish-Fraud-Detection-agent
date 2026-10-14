"""
Is This Legit? — scam-verification personal agent on TinyFish.

Paste a company / app name (and optionally its URL). The agent:
  1. Search   -> finds the official site (if no URL), the RBI Alert List page, and fraud/complaint reports
  2. Agent    -> opens the suspect site in a remote browser and extracts its claims;
                 navigates SEBI's intermediary search and looks the entity up
  3. Fetch    -> reads the RBI Alert List page and WHOIS record (domain age)
Then scores the evidence and returns a verdict with linked proof.

Run:  export TINYFISH_API_KEY=...  &&  streamlit run app.py
"""

import json
import re
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import streamlit as st
from tinyfish import TinyFish, RunStatus, BrowserProfile

client = TinyFish()  # reads TINYFISH_API_KEY

SEBI_START_URL = "https://www.sebi.gov.in/"
SCAM_WORDS = ("scam", "fraud", "fake", "cheat", "complaint", "ponzi", "warning", "unauthorised", "unauthorized", "lost money")
BLOCK_SIGNALS = ("captcha", "access denied", "blocked", "forbidden", "verify you are human")


# ---------------------------------------------------------------- helpers
def plain(obj):
    """SDK model -> plain python."""
    if hasattr(obj, "model_dump"):
        return plain(obj.model_dump())
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(x) for x in obj]
    return obj


def find_items(obj):
    """Collect every dict that carries a 'url' (search hits / fetch results)."""
    out = []
    if isinstance(obj, dict):
        if "url" in obj:
            out.append(obj)
        for v in obj.values():
            out += find_items(v)
    elif isinstance(obj, list):
        for v in obj:
            out += find_items(v)
    return out


def all_text(obj):
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return "\n".join(all_text(v) for v in obj.values())
    if isinstance(obj, list):
        return "\n".join(all_text(v) for v in obj)
    return ""


def parse_json(x):
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            m = re.search(r"\{.*\}", x, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    pass
        return {"raw": x}
    return {"raw": x}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def domain_of(url):
    host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------- TinyFish calls
def tf_search(query, **kw):
    try:
        return find_items(plain(client.search.query(query, location="IN", language="en", **kw)))
    except Exception as e:
        return [{"url": "", "title": f"search error: {e}", "snippet": ""}]


def tf_fetch(url):
    try:
        return all_text(plain(client.fetch.get_contents(urls=[url])))
    except Exception as e:
        return f"__fetch_error__ {e}"


def tf_agent(url, goal, stealth=False):
    kw = {"browser_profile": BrowserProfile.STEALTH} if stealth else {}
    try:
        r = client.agent.run(url=url, goal=goal, **kw)
    except Exception as e:
        return {"_error": str(e)}
    if r.status != RunStatus.COMPLETED:
        return {"_error": f"run status {r.status}"}
    data = parse_json(plain(r.result))
    if any(s in json.dumps(data).lower() for s in BLOCK_SIGNALS):
        data["_blocked"] = True
    return data


# ---------------------------------------------------------------- checks
def resolve_url(name):
    for hit in tf_search(f"{name} official website"):
        if hit.get("url"):
            return hit["url"]
    return None


def inspect_site(url):
    goal = (
        "You are a fraud investigator. Read this website (home page plus any About/Contact/Plans/Returns page "
        "reachable in one click). Return ONLY JSON with keys: "
        "company_name (string), "
        "claimed_registrations (list of strings, e.g. 'SEBI registered', 'RBI approved', 'ISO'), "
        "registration_numbers (list of any registration/licence numbers shown), "
        "promised_returns (string, exact claim about returns/earnings or ''), "
        "guaranteed_returns (true if returns/profit/income are promised or guaranteed), "
        "upfront_payment_requested (true if it asks for deposit/registration/processing fee before service), "
        "contact_channels (list: whatsapp, telegram, phone, email, physical address), "
        "red_flags (list of short strings for urgency, pressure, too-good-to-be-true, missing address, etc.)."
    )
    return tf_agent(url, goal, stealth=True)


def sebi_check(name):
    goal = (
        f"Find whether '{name}' is a SEBI-registered intermediary. Navigate to the Intermediaries section "
        "('Recognised Intermediaries' / 'Registered Intermediaries'). Search the categories Stock Brokers, "
        "Investment Advisers, Research Analysts and Portfolio Managers for the name (try the shortest distinctive "
        "word of the name if the full name returns nothing). Return ONLY JSON: "
        '{"found": true/false, "matches": [{"name": "", "registration_no": "", "category": ""}], '
        '"page_url": "url of the result page"}'
    )
    return tf_agent(SEBI_START_URL, goal)


def rbi_alert_check(name, domain):
    hits = tf_search("RBI Alert List unauthorised forex trading platforms site:rbi.org.in")
    page = next((h["url"] for h in hits if "rbi.org.in" in h.get("url", "")), None)
    if not page:
        return {"checked": False, "listed": False, "page_url": None}
    text = norm(tf_fetch(page))
    keys = [k for k in {norm(name), norm(domain.split(".")[0]) if domain else ""} if len(k) >= 4]
    return {"checked": len(text) > 500, "listed": any(k in text for k in keys), "page_url": page}


def domain_age(domain):
    if not domain:
        return {"days": None, "created": None, "source": None}
    src = f"https://www.whois.com/whois/{domain}"
    txt = tf_fetch(src)
    m = re.search(r"(?:Creation Date|Registered On|Created On|created)\s*:?\s*(\d{4}-\d{2}-\d{2})", txt, re.I)
    if not m:
        return {"days": None, "created": None, "source": src}
    created = dt.date.fromisoformat(m.group(1))
    return {"days": (dt.date.today() - created).days, "created": str(created), "source": src}


def complaints(name):
    seen, out = set(), []
    for q, kw in ((f'"{name}" scam OR fraud OR complaint', {}), (f"{name} fraud", {"domain_type": "news"})):
        for h in tf_search(q, **kw):
            blob = f"{h.get('title', '')} {h.get('snippet', '')}".lower()
            if h.get("url") and h["url"] not in seen and any(w in blob for w in SCAM_WORDS):
                seen.add(h["url"])
                out.append({"title": h.get("title", h["url"]), "url": h["url"], "snippet": h.get("snippet", "")})
    return out[:6]


# ---------------------------------------------------------------- scoring
def score(site, sebi, rbi, age, reports):
    pts, ev = 0, []

    def add(p, sev, text, link=None):
        nonlocal pts
        pts += p
        ev.append({"severity": sev, "finding": text, "source": link or ""})

    if rbi.get("listed"):
        add(70, "🔴", "Name/domain appears on RBI Alert List of unauthorised forex platforms", rbi["page_url"])
    elif rbi.get("checked"):
        add(0, "🟢", "Not found on RBI Alert List", rbi["page_url"])

    claims_sebi = any("sebi" in c.lower() for c in site.get("claimed_registrations", []) or [])
    if sebi.get("found"):
        m = (sebi.get("matches") or [{}])[0]
        add(-40, "🟢", f"SEBI registered: {m.get('name', '')} {m.get('registration_no', '')} ({m.get('category', '')})", sebi.get("page_url"))
    elif sebi.get("_error") or sebi.get("_blocked"):
        add(0, "🟡", "SEBI registry could not be checked this run", SEBI_START_URL)
    elif claims_sebi:
        add(45, "🔴", "Site claims SEBI registration but no match in SEBI intermediary database", sebi.get("page_url"))
    else:
        add(10, "🟡", "Not a SEBI-registered intermediary", sebi.get("page_url"))

    if site.get("guaranteed_returns"):
        add(25, "🔴", f"Promises/guarantees returns: {site.get('promised_returns') or 'yes'}")
    if site.get("upfront_payment_requested"):
        add(15, "🔴", "Asks for upfront deposit/registration fee")
    ch = [c.lower() for c in site.get("contact_channels", []) or []]
    if ch and all(c in ("whatsapp", "telegram") for c in ch):
        add(15, "🔴", "Only WhatsApp/Telegram contact, no address")
    for f in (site.get("red_flags") or [])[:3]:
        add(5, "🟡", f"Site red flag: {f}")
    if site.get("_blocked") or site.get("_error"):
        add(5, "🟡", "Site blocked automated inspection")

    d = age.get("days")
    if d is not None:
        if d < 30:
            add(30, "🔴", f"Domain created {d} days ago ({age['created']})", age["source"])
        elif d < 180:
            add(20, "🟡", f"Domain only {d} days old ({age['created']})", age["source"])
        else:
            add(0, "🟢", f"Domain age {d // 365}y {d % 365 // 30}m ({age['created']})", age["source"])

    if reports:
        add(min(30, 10 * len(reports)), "🔴" if len(reports) >= 2 else "🟡", f"{len(reports)} fraud/complaint reports found online")

    pts = max(0, min(100, pts))
    verdict = "🔴 LIKELY SCAM" if pts >= 60 else "🟡 UNVERIFIED — be careful" if pts >= 30 else "🟢 LOOKS LEGIT"
    return pts, verdict, ev


# ---------------------------------------------------------------- pipeline
def investigate(name, url, log):
    if not url:
        log("Search: locating official website…")
        url = resolve_url(name)
    domain = domain_of(url) if url else ""
    log(f"Target: {url or 'no website found'}")

    with ThreadPoolExecutor(max_workers=5) as ex:
        log("Running in parallel: site inspection (Agent), SEBI lookup (Agent), RBI Alert List (Search+Fetch), WHOIS (Fetch), complaints (Search)…")
        f_site = ex.submit(inspect_site, url) if url else None
        f_sebi = ex.submit(sebi_check, name)
        f_rbi = ex.submit(rbi_alert_check, name, domain)
        f_age = ex.submit(domain_age, domain)
        f_rep = ex.submit(complaints, name)
        site = f_site.result() if f_site else {"_error": "no website"}
        sebi, rbi, age, reports = f_sebi.result(), f_rbi.result(), f_age.result(), f_rep.result()

    pts, verdict, ev = score(site, sebi, rbi, age, reports)
    return {"name": name, "url": url, "risk": pts, "verdict": verdict, "evidence": ev,
            "reports": reports, "raw": {"site": site, "sebi": sebi, "rbi": rbi, "whois": age}}


# ---------------------------------------------------------------- UI
st.set_page_config(page_title="Is This Legit?", page_icon="🛡️", layout="wide")
st.title("🛡️ Is This Legit?")
st.caption("Personal agent that checks a trading app, loan app, investment scheme or job offer against SEBI & RBI registries, domain age and fraud reports — powered by TinyFish.")

c1, c2 = st.columns([2, 3])
name = c1.text_input("Company / app / scheme name", placeholder="e.g. XYZ Capital")
url = c2.text_input("Website or link (optional)", placeholder="https://…")

if st.button("Investigate", type="primary", disabled=not name.strip()):
    with st.status("Agent investigating…", expanded=True) as status:
        res = investigate(name.strip(), url.strip() or None, st.write)
        status.update(label="Investigation complete", state="complete")
    st.session_state.setdefault("history", []).insert(0, res)

for i, res in enumerate(st.session_state.get("history", [])):
    with st.container(border=True):
        a, b = st.columns([3, 1])
        a.subheader(f"{res['verdict']}  ·  {res['name']}")
        a.caption(res["url"] or "")
        b.metric("Risk score", f"{res['risk']}/100")
        st.dataframe(res["evidence"], use_container_width=True, hide_index=True,
                     column_config={"source": st.column_config.LinkColumn("source")})
        if res["reports"]:
            st.markdown("**Reports found online**")
            for r in res["reports"]:
                st.markdown(f"- [{r['title']}]({r['url']})")
        if res["risk"] >= 30:
            st.warning("Don't pay or share OTP/KYC. Report at **cybercrime.gov.in** or call **1930**. "
                       "Report unregistered investment advice at **scores.sebi.gov.in**.")
        with st.expander("Raw agent output"):
            st.json(res["raw"])
    if i == 0 and len(st.session_state["history"]) > 1:
        st.markdown("#### Earlier checks")
