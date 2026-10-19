import calendar
import re

_FED_ID = re.compile(r"^KXFED[A-Z]*-(\d\d)([A-Z]{3})")


def _days(a: dict, b: dict) -> float:
    return abs((a["close_time"] - b["close_time"]).total_seconds()) / 86400


def rule_pairs(kalshi: list[dict], pm: list[dict]) -> list[tuple[str, str, float]]:
    out = []
    for k in kalshi:
        m = _FED_ID.match(k["event_id"] or "")
        if k["category"] != "fed_macro" or not m:
            continue
        year = 2000 + int(m[1])
        month = {n.upper(): i for i, n in enumerate(calendar.month_abbr)}.get(m[2])
        if not month:
            continue
        name = calendar.month_name[month]
        for p in pm:
            if p["category"] != "fed_macro" or name.lower() not in p["title"].lower() or _days(k, p) > 2:
                continue
            # year must be in the title, else fall back to the Polymarket close_time year
            years = re.findall(r"\b(20\d\d)\b", p["title"]) or [str(p["close_time"].year)]
            if str(year) in years:
                out.append((k["market_id"], p["market_id"], 1.0))
    return out


def embed_pairs(kalshi, pm, kvec, pvec, k=5, min_sim=0.75, max_days=31):
    sims = kvec @ pvec.T
    out = []
    for i, kr in enumerate(kalshi):
        same = [j for j, p in enumerate(pm) if p["category"] == kr["category"]]
        for j in sorted(same, key=lambda j: -sims[i, j])[:k]:
            if sims[i, j] >= min_sim and _days(kr, pm[j]) <= max_days:
                out.append((kr["market_id"], pm[j]["market_id"], float(sims[i, j])))
    return out


def merge(rule: list, emb: list) -> list[tuple[str, str, float, str]]:
    out = {(a, b): (a, b, s, "embed") for a, b, s in emb}
    out.update({(a, b): (a, b, s, "rule") for a, b, s in rule})
    return list(out.values())
