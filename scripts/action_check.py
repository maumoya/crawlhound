"""Pass/fail for the crawlhound GitHub Action: reads map.json and items.jsonl from CH_OUT.

Env: CH_OUT, CH_MIN_PAGES, CH_MIN_ITEMS, CH_SITEMAP ("true" = every sitemap URL must be reached).
Writes outputs (pages, items, report) and the Markdown report to the job summary. Exit 1 on a miss."""
import json
import os
import sys
from pathlib import Path


def main() -> int:
    out = Path(os.environ.get("CH_OUT", "crawlhound-report"))
    rep = json.loads((out / "map.json").read_text())
    cov = rep["coverage"]
    items_file = out / "items.jsonl"
    items = sum(1 for line in items_file.read_text().splitlines() if line.strip()) if items_file.exists() else 0
    pages = cov["pages"]
    misses = []
    if pages < int(os.environ.get("CH_MIN_PAGES", "1")):
        misses.append(f"{pages} pages reached, need {os.environ['CH_MIN_PAGES']}")
    if items < int(os.environ.get("CH_MIN_ITEMS", "0")):
        misses.append(f"{items} items extracted, need {os.environ['CH_MIN_ITEMS']}")
    if os.environ.get("CH_SITEMAP") == "true" and cov["sitemap_not_reached"]:
        misses.append(f"{len(cov['sitemap_not_reached'])} sitemap URLs never reached: {', '.join(cov['sitemap_not_reached'][:5])}")
    gh_out, summary = os.environ.get("GITHUB_OUTPUT"), os.environ.get("GITHUB_STEP_SUMMARY")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"pages={pages}\nitems={items}\nreport={out / 'map.md'}\n")
    line = f"crawlhound: {pages} pages, {items} items, {len(rep['blocked_requests'])} requests blocked, {cov['verdict']}"
    if summary:
        with open(summary, "a") as fh:
            fh.write(f"### {'✅' if not misses else '❌'} {line}\n\n" + "".join(f"- {m}\n" for m in misses) + "\n")
            fh.write((out / "map.md").read_text())
    print(line)
    for m in misses:
        print(f"::error::{m}")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
