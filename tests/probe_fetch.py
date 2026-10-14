import sys
sys.path.insert(0, "tests")
import smoke_env  # noqa
from tinyfish import TinyFish
c = TinyFish(timeout=150, max_retries=0)
r = c.fetch.get_contents(urls=sys.argv[1:])
for x in r.results:
    t = x.text or ""
    print("==", x.url, "len", len(t), "title", x.title)
    print(t[:1500].replace("\n\n", "\n"))
    print("... TAIL:", t[-600:].replace("\n\n", "\n"))
print("ERRORS", r.errors)
