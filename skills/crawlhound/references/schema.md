# Schema format

A schema is a small JSON file. `spider.py --check-schema FILE` validates it offline.

```json
{
  "name": "quotes",
  "description": "optional note",
  "match": "/page/[0-9]+",
  "mode": "css",
  "item": ".quote",
  "fields": {
    "text":   {"css": ".text"},
    "author": {"css": ".author"},
    "tags":   {"css": ".tag", "all": true},
    "link":   {"css": "a", "attr": "href"},
    "price":  {"css": ".price", "regex": "[0-9.]+", "type": "number"},
    "whole":  {}
  }
}
```

| Key | Meaning |
|---|---|
| `name` | Label written into every item as `_schema`. Default `items`. |
| `match` | Regex on the page-state URL. States that do not match are not extracted (the crawl still visits them). |
| `mode` | `css` (default), `tables` (every `<table>`: caption, header, rows) or `jsonld` (JSON-LD objects). |
| `item` | CSS selector; one item per match. Default `html` = one item per page state. |
| `fields` | Field name -> spec. Required in `css` mode. |
| `jsonld_types` | `jsonld` mode: keep objects whose `@type` is in this list (default: all). |

Field spec keys:

| Key | Meaning |
|---|---|
| `css` | Selector inside the item. No `css` = the item element itself. `a || b` = fallbacks, first that matches wins. |
| `attr` | Read an attribute instead of the text. `href`, `src`, `action`, `poster`, `data-src` become absolute URLs. `a|b` = fallbacks. |
| `all` | `true` = list of every match instead of the first. |
| `regex` | Keep only the match (group 1 if the regex has a group). No match = null. |
| `type` | `text` (default), `number` or `int` (commas and currency symbols are stripped). |

Every item also carries `_url` and `_state` (the SPA state fingerprint). Identical items are kept once.
Values are capped at 4000 characters and 2000 items per state.

The page side only reads the DOM and returns strings: regex and type conversion run in Python, so a schema
cannot make the page do anything.

## Writing a schema from a plain-English ask

1. Name the record: "each book with title, price and stock" -> item = the card, fields = title, price, stock.
2. Find the container that repeats once per record (`article.product_pod`, `.quote`, `tr`).
3. For each field, the shortest selector inside the container; prefer `itemprop`, then stable class names,
   then tag names. Avoid generated class names (`css-1x2y3z`).
4. Numbers: add `"regex"` and `"type": "number"`. Links and images: `"attr": "href"` / `"attr": "src|data-src"`.
5. Validate, trial-run on 1-3 pages, read `field_fill`, fix the zeros, then run the full crawl.

## Presets

| Preset | Pulls |
|---|---|
| `text` | title, h1, headings, main text per page |
| `tables` | every HTML table |
| `links` | every link: text, absolute URL, rel |
| `prices` | elements that look like prices: raw text, amount, currency |
| `product` | product cards: name, price, stock, URL, image |
| `article` | JSON-LD Article/NewsArticle/BlogPosting plus headline, byline, date, body |

There is no contacts preset: harvesting emails and phone numbers is personal data, and the skill refuses it.
