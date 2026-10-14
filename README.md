# 🛡️ Is This Legit? A scam-verification personal agent (TinyFish)

Indians lose crores every year to fake trading apps, loan apps, "work from home" job sites and "guaranteed return" schemes. Checking one properly means visiting SEBI's intermediary register, RBI's Alert List, a WHOIS service and complaint forums. Nobody does that, so this agent does it for you.

**Input:** a company, app or scheme name, with an optional link.
**Output:** a verdict (🔴 / 🟡 / 🟢), a 0–100 risk score, and an evidence table where every finding links to its source.

Built for the TinyFish × DTU buildathon (Personal Agents track).

## Setup

Requires **Python 3.11+**. The TinyFish SDK uses `enum.StrEnum`, so it will not import on Python 3.10.

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
# edit .env and set TINYFISH_API_KEY=... (get a key at https://agent.tinyfish.ai/api-keys)
streamlit run app.py
```

`.env` is gitignored. The app reads the key from the environment or from `.env` and never prints it.

Command-line version (same pipeline, prints the evidence and saves JSON):

```bash
python checks.py "Zerodha" --out demo/zerodha.json
python checks.py "Olymp Trade" https://olymptrade.com
```

## How TinyFish is used

Every call feeds the verdict. There are no decorative calls.

| Endpoint | Exact role | Code |
|---|---|---|
| **Agent** (stealth browser) | Opens the suspect website in a remote stealth browser, reads the home page and returns a fixed JSON schema: claimed registrations, registration numbers, promised returns, whether returns are guaranteed, upfront fees, contact channels and red flags. Reports a captcha instead of trying to solve it. | `inspect_site` |
| **Agent** | Goes straight to SEBI's register pages for **Stock Brokers (equity)** and **Investment Advisers**, types the name into the search box, clicks GO and returns the matching names and registration numbers. Both categories run in parallel, in about 55s. | `sebi_check` |
| **Search** | Finds the official website when no URL is given, skipping aggregators like Wikipedia and Play Store. | `resolve_url` |
| **Search** | Finds the current RBI Alert List page using `include_domains=rbi.org.in`. | `rbi_alert_check` |
| **Search** | Finds fraud and complaint reports with one web query and one news query. A hit only counts if it names the entity and contains a scam word. The company's own domain is excluded. | `complaints` |
| **Fetch** | Reads the RBI Alert List page. The table of about 95 entities, each with name and website, is parsed and matched on exact name, a whole-phrase name or the exact domain. | `rbi_alert_check` |
| **Fetch** | Reads the WHOIS record from whois.com, falling back to rdap.org, to get the domain creation date. Works for .com, .in and .co.in. | `domain_age` |

All five checks run in parallel. Timeouts follow TinyFish's guidance: Agent 120s, Fetch 150s, Search 10s. A check that fails, times out or hits a captcha never crashes the run. It shows up as 🟡 "Could not verify" in the evidence table.

### Scoring (transparent, rule-based)

| Signal | Points |
|---|---|
| On RBI Alert List (name or domain) | +70 |
| Claims SEBI registration but not in SEBI's register | +45 |
| Not a SEBI-registered broker or adviser | +15 |
| SEBI-registered broker or adviser | −40 |
| Guaranteed or fixed returns promised | +25 |
| Upfront fee or deposit requested | +15 |
| Only WhatsApp/Telegram contact | +15 |
| Each red flag on the site (max 3) | +5 |
| Domain younger than 90 days / 1 year | +30 / +15 |
| Fraud reports online | +8 each, max 25 (+3 each if SEBI-registered) |
| A check that could not be verified | +5 |

0–24 is 🟢 LOOKS LEGIT, 25–59 is 🟡 UNVERIFIED, and 60+ is 🔴 LIKELY SCAM.

## Demo results

DEMO_TABLE

Full JSON for each run is in [`demo/`](demo/). The app's **Run demo set** button runs these three cases one after another.

## Known limitations

- **SEBI coverage:** only Stock Brokers (equity) and Investment Advisers are searched. Research analysts, portfolio managers and others are not checked yet. SEBI's search is POST-only, so the Agent has to drive the form, which takes 50–60s.
- **Agent variance:** Agent runs sometimes exceed 120s when several run at once. That check then shows as "Could not verify" rather than a guess.
- **RBI Alert List** covers unauthorised **forex** platforms only. Not being on it does not prove legitimacy, and RBI says so too.
- **Fraud reports** are keyword-matched. Big legitimate brands attract "X warns of fraud" articles, which is why reports weigh less once SEBI confirms registration.
- **Site inspection** reads only the home page, to stay inside the time budget.
- **WHOIS** pages can show a bot check. The parser still reads the date when it is present and otherwise falls back to RDAP.
- This is a rule-based screening aid, not legal or financial advice.

## Post (LinkedIn / X)

> Built a personal agent at the TinyFish × DTU buildathon: **"Is This Legit?"** 🛡️
> Paste any trading app, loan app or "earn ₹5000/day" job link. It opens the site in a remote stealth browser, searches SEBI's broker and adviser register, checks RBI's Alert List, reads WHOIS for domain age and searches fraud reports. Then it gives a 🔴/🟡/🟢 verdict where every finding links to its proof.
> A check that takes 20 minutes by hand now takes about 2.
> Built on @TinyFish Agent + Search + Fetch. #TinyFish #buildathon #fraudprevention #AIagents
