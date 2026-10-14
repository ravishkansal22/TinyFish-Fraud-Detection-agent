# 🛡️ Is This Legit? — Scam-verification personal agent (TinyFish)

Indians lose crores every year to fake trading apps, loan apps and "guaranteed return" schemes. Verifying one properly means visiting SEBI's intermediary search, RBI's Alert List, WHOIS and complaint forums. Nobody does it, so this agent does it for you.

**Input:** a name (with an optional link). **Output:** a verdict (🔴 / 🟡 / 🟢) with a risk score and linked evidence.

## How TinyFish is used

| Endpoint | What it does in the agent |
|---|---|
| **Agent** | Opens the suspect website in a remote stealth browser (nothing runs on your machine) and extracts claimed registrations, promised returns, upfront fee requests and contact channels. Then **navigates SEBI's intermediary registry**, searches broker, investment adviser, research analyst and portfolio manager categories, and returns any matches. |
| **Search** | Finds the official site when no URL is given, finds the current RBI Alert List page, and finds fraud and complaint reports (web and news). |
| **Fetch** | Reads the RBI Alert List page to check whether the name or domain is listed, and reads the WHOIS record to get domain age. |

All five checks run in parallel, and a transparent rule-based score combines the evidence.

## Run
```bash
pip install -r requirements.txt
export TINYFISH_API_KEY=your_key      # Windows: set TINYFISH_API_KEY=your_key
streamlit run app.py
```

## Demo script (3 runs)
1. **Fake:** pick a name from the RBI Alert List → 🔴 (on the Alert List, not registered with SEBI).
2. **Genuine:** Zerodha → 🟢 (SEBI registered, domain is several years old).
3. **Unclear:** a random "earn ₹5000/day" job link from spam → 🟡 or 🔴 (new domain, guaranteed income, WhatsApp-only contact).

## Post (LinkedIn / X)
> Built a personal agent at the TinyFish × DTU buildathon: **"Is This Legit?"** Paste any trading app, loan app or job-offer link. It opens the site in a remote browser, looks the company up in SEBI's registry, checks RBI's Alert List, reads WHOIS for domain age, and searches fraud reports, then gives a verdict with proof. One check that used to take 20 minutes now takes about 60 seconds. Built on @TinyFish Agent + Search + Fetch. #TinyFish #buildathon #fraudprevention
