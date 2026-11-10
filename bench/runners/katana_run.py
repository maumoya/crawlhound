"""Run Katana (projectdiscovery/katana, MIT) headless on one target. Katana maps URLs, forms and XHR; it has no
field extraction, so items stay empty and are scored as not supported."""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import UA, save, target  # noqa: E402

IMAGE = "projectdiscovery/katana:v1.1.3@sha256:ecf5107b693955fba6edbbf489057e1ea075ace60fadfc6d8441ac45384ee279"


def main() -> None:
    name = sys.argv[1]
    t = target(name, sys.argv[2] if len(sys.argv) > 2 else "")
    cmd = ["docker", "run", "--rm", "--add-host", "host.lima.internal:192.168.5.2", IMAGE,
           "-u", t["start"], "-hl", "-nos", "-scp", "/usr/bin/chromium", "-xhr", "-fx", "-cs", t["scope"], "-rl", "1", "-c", "1", "-p", "1",
           "-d", str(t.get("depth", 3)), "-j", "-or", "-ob", "-silent", "-H", f"User-Agent: {UA}", "-timeout", "20"]
    began = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    seconds = round(time.time() - began, 1)
    rows = []
    for line in proc.stdout.splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    scope = re.compile(t["scope"])
    pages = {r["request"]["endpoint"] for r in rows if scope.search(r.get("request", {}).get("endpoint", ""))}
    forms = {json.dumps(f, sort_keys=True) for r in rows for f in (r.get("response") or {}).get("forms") or []}
    apis = {x.get("url") for r in rows for x in (r.get("response") or {}).get("xhr_requests") or []
            if x.get("method", "GET") == "GET"}
    note = "headless hybrid crawl, -rl 1 -c 1; no field extraction (items n/a); does not apply robots.txt rules"
    save("katana", name, {"pages": len(pages), "forms": len(forms), "apis": len(apis), "seconds": seconds,
                          "requests": len(rows), "status": "ok" if rows else "error",
                          "items": None, "note": note if rows else note + "; " + proc.stderr[-300:]})


if __name__ == "__main__":
    main()
