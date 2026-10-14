import os
from pathlib import Path
f = Path(__file__).resolve().parent.parent / ".env"
if f.exists():
    for line in f.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
