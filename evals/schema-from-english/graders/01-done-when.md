---
type: llm
focus: trace
weight: 1
---

The schema uses item `article.product_pod` (or an equivalent selector matching one element per book), reads the
title from the link's `title` attribute (not the truncated link text), converts price with `"type": "number"`, and
the trace shows `spider.py --check-schema` printing `OK`.
