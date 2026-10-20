# Embedding bake-off: bge-small (CPU) vs Qwen3-Embedding-4B (Colab T4), 2026-10-19

**Question.** Does candidate generation miss real Kalshi/Polymarket pairs because the production embedding model
(`BAAI/bge-small-en-v1.5`, 384-d, fastembed on CPU) can't separate numbers, dates and thresholds? Would a larger model
(`Qwen/Qwen3-Embedding-4B`, 2560-d, fp16 on a free Colab T4) find them?

**Answer: no.** The larger model does worse on the crypto near-pairs, and both models miss most of them for the
same reason: dense threshold ladders. Keep bge-small. The fix belongs in candidate generation (a structured crypto
rule, or a larger k), not in the embedding model. Sam confirmed the truth set's definition on 2026-10-20.

## Setup

| | |
|---|---|
| Snapshot | 5,006 current listings (last_seen within 2 h, category set), `data/bakeoff/listings.parquet`, sha256 `577aaff857d8…6262` |
| Text | `matching.embed.text_for(row)`: `"{title} \| {yes_label} \| closes {YYYY-MM-DD}"`, identical to production |
| Truth set | `labels/crypto_near_pairs.csv`: 312 `crypto_above` pairs (147 Kalshi markets), 15 `fed_exact` pairs |
| bge-small | revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, sentence-transformers fp16, no prefix (as fastembed) |
| Qwen3-4B | revision `5cf2132abc99cad020ac570b19d031efec650f2b`, fp16 + sdpa, left padding, the model card's instruction prefix on every text (symmetric task) |
| Candidates | production `matching.candidates.embed_pairs` (same category, k=5, min_sim=0.75, max_days=31); recall@k reruns it with min_sim off |
| MLflow | experiment `venue-arb`: bge-small `2ef6258e3e784c78becae3b950935f48`, qwen3-4b `659316d238aa4c8093bd4c66058a258b` |

Truth groups. `crypto_above`: Kalshi KXBTCD/KXETHD "≥ X" vs Polymarket "above X" with the same asset and ET date
(Kalshi CF Benchmarks RTI at 2am/3am/5pm ET; Polymarket Binance at noon or at the hour). No range pairs exist: Kalshi
range bins are $100–$500 wide, Polymarket's are $2,000. 312 ≥ 20, so the "nearest threshold" fallback group was
not needed. `fed_exact`: all 15 pairs in `labels/known_pairs.csv`, all present in the snapshot.

## Results

`kalshi hit` = share of truth Kalshi markets with at least one true partner among their candidates. A Kalshi
market has up to 4 true partners (noon daily plus the 1/2/3 AM hourly ladders on the same date), so pair recall at
k=1 is capped well below 1.

| model | group | pairs | recall (prod) | kalshi hit (prod) | recall@1 | recall@5 | recall@10 | recall@25 | candidates (prod) | embed s (T4) |
|---|---|---|---|---|---|---|---|---|---|---|
| bge-small | crypto_above | 312 | 105/312 = 0.337 | 0.612 | 0.215 | 0.337 | 0.385 | 0.532 | 12,547 | 2.6 |
| bge-small | fed_exact | 15 | 10/15 = 0.667 | 0.667 | 0.600 | 0.667 | 1.000 | 1.000 | 12,547 | 2.6 |
| bge-small | all | 327 | 115/327 = 0.352 | 0.617 | 0.232 | 0.352 | 0.413 | 0.554 | 12,547 | 2.6 |
| qwen3-4b | crypto_above | 312 | 55/312 = 0.176 | 0.293 | 0.019 | 0.176 | 0.327 | 0.494 | 12,088 | 172.2 |
| qwen3-4b | fed_exact | 15 | 15/15 = 1.000 | 1.000 | 0.600 | 1.000 | 1.000 | 1.000 | 12,088 | 172.2 |
| qwen3-4b | all | 327 | 70/327 = 0.214 | 0.358 | 0.046 | 0.214 | 0.358 | 0.517 | 12,088 | 172.2 |

Candidate counts are embedding candidates only (production also merges the Fed rule pairs). Model load on the T4:
15.5 s (bge-small), 90.0 s (Qwen3-4B); peak GPU memory 0.17 GB and 9.6 GB.

**What the numbers say.**

- min_sim 0.75 never binds on the truth set: production recall equals recall@5 for both models. k=5 is the limit.
- bge-small gets the threshold and the date right when it gets the top-1 right (e.g. "$74,000 or above, Oct 20" →
  "above $74,000 on October 20", cosine 0.97), but the Polymarket ladders on the same date differ only in the number,
  so the true partners compete with neighbouring thresholds and with the same threshold on other dates.
- Qwen3-4B matches the threshold but loses the date: its top-1 for the Oct 20 Kalshi markets is typically the same
  threshold on **Oct 26** (cosine 0.89–0.91). That is why its recall@1 is 0.02. It does separate the Fed brackets
  better (15/15 at k=5 vs 10/15), but the Fed pairs are already found by `rule_pairs`, so that gain is moot in
  production.
- Cost: Qwen3-4B needs a GPU (172 s for 5k short texts on a T4, vs 2.6 s for bge-small on the same GPU) and would put Colab in
  the live path, which the platform rules forbid. bge-small on CPU only embeds new listing versions each hour.

## Recommendation

Keep `bge-small-en-v1.5`; do not switch to Qwen3-Embedding-4B. Raising k alone buys little (bge-small crypto recall
0.34 → 0.39 at k=10 → 0.53 at k=25) while candidates scale roughly with k (12.5k at k=5, and the adjudicator takes
at most 500 per run), so it is not worth it either. The better move is a deterministic crypto rule next to the Fed
`rule_pairs`: the parser in `venue_arb/bakeoff/truth.py` already finds these pairs exactly (asset, ET date,
direction, threshold); as a rule it would find this group by construction (the truth set came from the same parser,
so that is not an independent measurement) at no LLM cost, leaving the embeddings for the long tail.
Before acting on any of this, Sam should confirm the truth set (in particular whether a Kalshi 5pm market and a
Polymarket 1 AM hourly market on the same date count as a near-pair).

## Caveats

- The truth set is built by a parser, not hand-checked; Sam confirmed its definition (2026-10-20). The
  `crypto_above` group is many-to-many by construction (same date, any time of day), which makes pair recall at small k look harsh; `kalshi hit` is the
  kinder number.
- One snapshot (2026-10-19, ~23:40 PT). Kalshi crypto markets on that day cover only Oct 20 and Oct 23.
- bge-small was embedded on the T4 with sentence-transformers fp16, not with production fastembed ONNX on CPU.
  Same weights, but small numeric differences are possible; not checked vector by vector.
- Qwen3 used one instruction prompt; another prompt, or no prompt, might change its date handling. Not tuned.
