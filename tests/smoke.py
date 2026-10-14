"""Smoke-test each TinyFish endpoint and dump the raw response structure.

Usage:  .venv/Scripts/python tests/smoke.py [search] [fetch] [agent]
Full responses are written to tests/smoke_out/<name>.json (gitignored).
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "smoke_out"


def load_env():
    """Load KEY=VALUE lines from .env without printing anything."""
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
if not os.environ.get("TINYFISH_API_KEY"):
    sys.exit("TINYFISH_API_KEY is not set (env or .env). Aborting.")
print("TINYFISH_API_KEY: set")

from tinyfish import AgentConfig, RunStatus, TinyFish  # noqa: E402


def shape(obj, depth=0, maxlen=300):
    """Print-friendly structure: truncate long strings and long lists."""
    if isinstance(obj, dict):
        return {k: shape(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        out = [shape(x, depth + 1) for x in obj[:3]]
        if len(obj) > 3:
            out.append(f"... (+{len(obj) - 3} more)")
        return out
    if isinstance(obj, str) and len(obj) > maxlen:
        return obj[:maxlen] + f"... [{len(obj)} chars]"
    return obj


def dump(name, resp, secs):
    data = resp.model_dump(mode="json")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n===== {name}  ({secs:.1f}s)  type={type(resp).__name__} =====")
    print(json.dumps(shape(data), indent=2, ensure_ascii=False))


which = set(sys.argv[1:]) or {"search", "fetch", "agent"}

if "search" in which:
    c = TinyFish(timeout=10, max_retries=1)
    t = time.time()
    dump("search", c.search.query("Zerodha official website", location="IN", language="en"), time.time() - t)

if "fetch" in which:
    c = TinyFish(timeout=150, max_retries=0)
    t = time.time()
    dump("fetch", c.fetch.get_contents(urls=["https://www.whois.com/whois/zerodha.com"]), time.time() - t)

if "agent" in which:
    c = TinyFish(timeout=120, max_retries=0)
    t = time.time()
    r = c.agent.run(
        url="https://zerodha.com",
        goal="Read the home page. Return the company name and any SEBI registration number shown on the page.",
        output_schema={
            "type": "object",
            "properties": {
                "company_name": {"type": "string"},
                "sebi_registration_numbers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["company_name", "sebi_registration_numbers"],
        },
        agent_config=AgentConfig(max_duration_seconds=110),
    )
    dump("agent", r, time.time() - t)
    print("status == COMPLETED:", r.status == RunStatus.COMPLETED)
