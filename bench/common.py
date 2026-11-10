"""Shared bits for the benchmark runners: targets, the result file shape and the fixture origin."""
import json
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
TARGETS = json.loads((BENCH / "targets.json").read_text())
RESULTS = BENCH / "results"
UA = "crawlhound-bench/0.2 (+https://github.com/maumoya/crawlhound)"


def target(name: str, fixture_origin: str = "") -> dict:
    t = dict(TARGETS[name])
    t["start"] = t["start"].replace("{FIXTURE}", fixture_origin)
    t["scope"] = t["scope"].replace("{FIXTURE}", fixture_origin.replace(".", r"\."))
    return t


def save(tool: str, name: str, result: dict) -> Path:
    """result: items (list of dicts), pages, states, forms, apis, seconds, requests, status ('ok'|'not run'|'error'), note."""
    out = RESULTS / tool / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    base = {"tool": tool, "target": name, "date": time.strftime("%Y-%m-%d"), "items": [], "pages": None,
            "states": None, "forms": None, "apis": None, "seconds": None, "requests": None, "status": "ok", "note": ""}
    out.write_text(json.dumps({**base, **result}, indent=1, ensure_ascii=False))
    return out
