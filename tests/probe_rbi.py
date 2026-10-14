import os, sys, time
sys.path.insert(0, "tests")
import smoke_env  # noqa
from tinyfish import TinyFish
c = TinyFish(timeout=10, max_retries=1)
for q in ["RBI Alert List unauthorised forex trading platforms", "Alert List of unauthorised forex trading platforms and websites"]:
    r = c.search.query(q, location="IN", language="en", include_domains="rbi.org.in")
    print("Q:", q)
    for x in r.results[:6]:
        print("  ", x.url, "|", x.title[:80])
