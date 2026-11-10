"""Build the ground truth from the static (server-rendered) twins of each target, at 1 request per second.

quotes.toscrape.com serves the same 100 quotes as plain HTML at /page/N/ that /js/ and /scroll render with
JavaScript, so the static pages are the answer key. books.toscrape.com is static already. The fixture's truth is
the JSON its page script renders from. Writes bench/truth/<target>.json.
"""
import html
import json
import re
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = "crawlhound-bench/0.2 (+https://github.com/maumoya/crawlhound)"


def get(url: str) -> str:
    time.sleep(1.0)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def quotes() -> list:
    out = []
    for n in range(1, 11):
        page = get(f"https://quotes.toscrape.com/page/{n}/")
        for block in re.findall(r'<div class="quote".*?</div>\s*</div>', page, re.S):
            out.append({"text": clean(re.search(r'<span class="text"[^>]*>(.*?)</span>', block, re.S).group(1)),
                        "author": clean(re.search(r'<small class="author"[^>]*>(.*?)</small>', block, re.S).group(1)),
                        "tags": [clean(t) for t in re.findall(r'<a class="tag"[^>]*>(.*?)</a>', block, re.S)]})
    return out


def books() -> list:
    out = []
    for n in range(1, 6):
        page = get(f"https://books.toscrape.com/catalogue/page-{n}.html")
        for block in re.findall(r'<article class="product_pod">.*?</article>', page, re.S):
            out.append({"title": html.unescape(re.search(r'<h3><a [^>]*title="([^"]*)"', block).group(1)),
                        "price": float(re.search(r'class="price_color">[^0-9]*([0-9.]+)<', block).group(1)),
                        "stock": clean(re.search(r'class="instock availability">(.*?)</p>', block, re.S).group(1))})
    return out


def fixture() -> list:
    data = json.loads((HERE.parent / "tests/fixtures/site/api/products.json").read_text())
    return [{"name": p["name"], "price": float(p["price"].replace("$", "").replace(",", ""))} for p in data["products"]]


def main() -> None:
    q = quotes()
    truth = {"fixture": fixture(), "quotes-js": q, "quotes-scroll": q, "books": books()}
    for name, items in truth.items():
        (HERE / "truth" / f"{name}.json").write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n")
        print(f"{name}: {len(items)} items")


if __name__ == "__main__":
    main()
