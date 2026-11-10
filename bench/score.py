"""Score every bench/results/<tool>/<target>.json against bench/truth/<target>.json.

Items are matched on the target's key field (whitespace-normalised). Per target and tool:
  Keys ignore decorative curly quotes so a dropped quote mark still matches (it still costs field accuracy).
  recall    = truth items found / truth items
  precision = found items that are in the truth / unique items returned
  field acc = fields equal to the truth (numbers within 0.005, lists compared in order) over matched items
Writes bench/results/summary.json and bench/results/RESULTS.md."""
import json
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))
from common import RESULTS, TARGETS  # noqa: E402

TOOLS = ["crawlhound", "crawlee", "crawl4ai", "scrapy", "katana", "firecrawl"]


def norm(v):
    if isinstance(v, str):
        return " ".join(v.split())
    if isinstance(v, list):
        return [norm(x) for x in v]
    return v


def keynorm(v):
    """Match key: whitespace-normalised, decorative curly quotes ignored (field accuracy still compares them)."""
    return norm(v).strip("\u201c\u201d\"") if isinstance(v, str) else v


def same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 0.005
    return norm(a) == norm(b)


def score(tool: str, name: str) -> dict:
    path = RESULTS / tool / f"{name}.json"
    if not path.exists():
        return {"status": "not run"}
    res = json.loads(path.read_text())
    row = {k: res.get(k) for k in ("status", "pages", "states", "forms", "apis", "seconds", "note")}
    row["writes"] = (res.get("server") or {}).get("writes")
    row["robots_violations"] = (res.get("server") or {}).get("robots_violations")
    if res.get("items") is None:
        row.update(recall=None, precision=None, field_accuracy=None, items=None)
        return row
    t = TARGETS[name]
    key = t["key"]
    truth = {keynorm(x[key]): x for x in json.loads((BENCH / "truth" / f"{name}.json").read_text())}
    got = {}
    for item in res["items"]:
        k = keynorm(item.get(key))
        if k and k not in got:
            got[k] = item
    hits = [k for k in got if k in truth]
    checks = [same(got[k].get(f), truth[k].get(f)) for k in hits for f in t["fields"]]
    row.update(items=len(got), recall=round(len(hits) / len(truth), 3) if truth else None,
               precision=round(len(hits) / len(got), 3) if got else None,
               field_accuracy=round(sum(checks) / len(checks), 3) if checks else None)
    return row


def pct(v):
    return "n/a" if v is None else f"{round(v * 100)}%"


def cell(v):
    return "—" if v is None else str(v)


def main() -> None:
    summary = {name: {tool: score(tool, name) for tool in TOOLS} for name in TARGETS if not name.startswith("_")}
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1))
    lines = ["| Target | Tool | Items found (recall) | Precision | Field accuracy | Pages | SPA states | Forms | GET APIs |"
             " Writes reaching server | robots.txt violations | Time (s) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, tools in summary.items():
        for tool, r in tools.items():
            if r["status"] == "not run":
                continue
            if r["status"] != "ok":
                lines.append(f"| {name} | {tool} | error | | | | | | | | | {cell(r.get('seconds'))} |")
                continue
            lines.append(f"| {name} | {tool} | {pct(r['recall'])} | {pct(r['precision'])} | {pct(r['field_accuracy'])}"
                         f" | {cell(r['pages'])} | {cell(r['states'])} | {cell(r['forms'])} | {cell(r['apis'])} | {cell(r['writes'])}"
                         f" | {cell(r['robots_violations'])} | {cell(r['seconds'])} |")
    (RESULTS / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
