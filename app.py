"""
Is This Legit? -- scam-verification personal agent on TinyFish (Streamlit UI).

All TinyFish logic lives in checks.py. Run:
    streamlit run app.py        (needs TINYFISH_API_KEY in the environment or in .env)
"""
import json
from pathlib import Path

import streamlit as st

import checks

st.set_page_config(page_title="Is This Legit?", page_icon="🛡️", layout="wide")

DEMO_CASES = [
    ("Olymp Trade", "https://olymptrade.com"),       # on the RBI Alert List -> expect red
    ("Zerodha", ""),                                  # SEBI-registered broker -> expect green
    ("Rojgaar Bharat", "https://rojgaarbharat.com/"),  # "typing work from home" job site -> expect yellow/red
]
STATE_ICON = {"waiting": "⚪", "finding website": "🔎", "running": "⏳", "ok": "✅", "error": "⚠️", "blocked": "🚧", "partial": "◐"}
VERDICT_COLOR = {"🔴": "#d93025", "🟡": "#f9ab00", "🟢": "#188038"}

st.markdown("""
<style>
.verdict-card {border-radius: 14px; padding: 18px 22px; margin-bottom: 10px; color: white;}
.verdict-card h2 {margin: 0; color: white; font-size: 1.9rem;}
.verdict-card p {margin: 4px 0 0 0; opacity: .92;}
.riskbar {height: 14px; border-radius: 7px; background: rgba(255,255,255,.35); margin-top: 12px;}
.riskbar > div {height: 14px; border-radius: 7px; background: white;}
</style>""", unsafe_allow_html=True)

st.title("🛡️ Is This Legit?")
st.caption("A personal agent that checks a trading app, loan app, investment scheme or job offer against SEBI's "
           "register, RBI's Alert List, domain age and fraud reports. Powered by TinyFish Agent, Search and Fetch.")

if not checks.has_key():
    st.error("TINYFISH_API_KEY is not set. Add it to a `.env` file next to app.py "
             "(see `.env.example`) or to your environment, then restart the app.")
    st.stop()


# ---------------------------------------------------------------- running a case
def run_case(name: str, url: str, save_demo: bool = False) -> dict:
    with st.status(f"Investigating **{name}**…", expanded=True) as status:
        box = st.empty()

        def progress(states):
            running = [checks.CHECK_LABELS[k] for k, v in states.items() if v in ("running", "finding website")]
            box.markdown("\n".join(f"- {STATE_ICON.get(v, '•')} {checks.CHECK_LABELS[k]}: `{v}`"
                                   for k, v in states.items()))
            status.update(label=f"Investigating **{name}**: {len(running)} check(s) still running"
                          if running else f"Scoring **{name}**…")

        res = checks.investigate(name, url or None, progress=progress)
        failed = [e["check"] for e in res["evidence"] if e["finding"].startswith("Could not verify")]
        status.update(label=f"{res['verdict']} · {name} · {res['seconds']}s"
                      + (f" · could not verify: {', '.join(failed)}" if failed else ""),
                      state="complete", expanded=False)
    st.session_state.setdefault("history", []).insert(0, res)
    if save_demo:  # demo set results are kept as JSON evidence in demo/
        out = Path(__file__).parent / "demo" / f"{checks.norm(name)}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return res


# ---------------------------------------------------------------- rendering a result
def render(res: dict, key: str):
    sev = res["verdict"][:1]
    color = VERDICT_COLOR.get(sev, "#5f6368")
    st.markdown(f"""
<div class="verdict-card" style="background:{color}">
  <h2>{res['verdict']}</h2>
  <p><b>{res['name']}</b> · {res['url'] or 'no website found'} · risk score <b>{res['risk']}/100</b>
     · checked in {res['seconds']}s</p>
  <div class="riskbar"><div style="width:{max(res['risk'], 2)}%"></div></div>
</div>""", unsafe_allow_html=True)

    left, right = st.columns([3, 2])
    with left:
        st.markdown("**Evidence** (every finding links to its source)")
        st.dataframe(
            [{"": e["severity"], "check": e["check"], "finding": e["finding"], "points": e["points"],
              "source": e["source"] or None} for e in res["evidence"]],
            hide_index=True, use_container_width=True,
            column_config={"source": st.column_config.LinkColumn("source", display_text="open source ↗"),
                           "points": st.column_config.NumberColumn("pts", format="%+d"),
                           "finding": st.column_config.TextColumn("finding", width="large")})
    with right:
        st.markdown("**Reports found online**")
        if res["reports"]:
            for r in res["reports"]:
                st.markdown(f"- [{r['title']}]({r['url']}) <span style='opacity:.6'>· {r['site']}</span>",
                            unsafe_allow_html=True)
        else:
            st.caption("No fraud or complaint reports matched this name.")

    msg = ("**What to do next**\n"
           "- Don't pay any fee, deposit or \"tax\" and never share OTP, UPI PIN or KYC documents.\n"
           "- Lost money already? Call **1930** (National Cyber Crime Helpline) right away or file at "
           "[cybercrime.gov.in](https://cybercrime.gov.in).\n"
           "- Unregistered investment advice or a broker complaint: [scores.sebi.gov.in](https://scores.sebi.gov.in).\n"
           "- Check any adviser yourself: [SEBI intermediaries](https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognised=yes) "
           "· [RBI Alert List](https://rbi.org.in/scripts/bs_viewcontent.aspx?Id=4235)")
    (st.error if res["risk"] >= 60 else st.warning if res["risk"] >= 25 else st.info)(msg)

    c1, c2 = st.columns([1, 4])
    c1.download_button("Download JSON", json.dumps(res, indent=2, ensure_ascii=False, default=str),
                       file_name=f"{checks.norm(res['name']) or 'result'}.json", mime="application/json", key=f"dl{key}")
    with c2.expander("Raw TinyFish output"):
        st.json(res["raw"], expanded=False)


# ---------------------------------------------------------------- page
c1, c2 = st.columns([2, 3])
name = c1.text_input("Company / app / scheme name", placeholder="e.g. XYZ Capital")
url = c2.text_input("Website or link (optional: found with Search if empty)", placeholder="https://…")
b1, b2, _ = st.columns([1, 1, 4])
go = b1.button("Investigate", type="primary", disabled=not name.strip(), use_container_width=True)
demo = b2.button("Run demo set", use_container_width=True,
                 help="Runs 3 preset cases one after another: " + ", ".join(n for n, _ in DEMO_CASES))

if go:
    run_case(name.strip(), url.strip())
if demo:
    for n, u in DEMO_CASES:
        run_case(n, u, save_demo=True)

history = st.session_state.get("history", [])
for i, res in enumerate(history):
    if i == 1:
        st.markdown("### Earlier checks this session")
    if i == 0:
        render(res, str(i))
    else:
        with st.expander(f"{res['verdict']} · {res['name']} · risk {res['risk']}/100 · {res['checked_at']}"):
            render(res, str(i))
