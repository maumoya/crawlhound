---
tags: [crawlhound]
max_turns: 10
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write]
---

I want to crawl a bookshop with the crawlhound. Each book looks like files/books.html. Write me a schema that
grabs the full book title, the price as a number and whether it is in stock, and check that it is valid.
