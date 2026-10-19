# venue-arb agent rules

Follow `~/platform/.agent-rules/hard-rules.md` and the runbook `~/platform/docs/NEW_PROJECT.md`.

Project-specific rules:

- Read-only public endpoints only. No code calls any order, account, portfolio, or position endpoint on any venue.
- No VPN or non-US routing. Binance/Bybit are out of scope for plan 1.
- Pair convention everywhere: `a` is Kalshi, `b` is Polymarket.
- Postgres objects live in schema `venue_arb`, ClickHouse in database `venue_arb`. Migrations never `CREATE EXTENSION`.
- LLM spend stays under $20 per calendar month; no paid data sources.
- Commits carry no `Co-Authored-By` trailer. Never print or log secret values.
