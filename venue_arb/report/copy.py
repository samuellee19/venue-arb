"""The words the note and the dashboard's Research tab share. Pure: no qa, no I/O.

METHOD is rendered verbatim by `note.render` (Method section) and shipped in the bundle by the publisher, so both
say the same thing. HEADLINE holds str.format templates the reducer (`venue_arb.report.research`) fills.
"""
from venue_arb.config import BASIS_WINDOW_DAYS

METHOD: dict[str, str] = {
    "matching": (
        "Every listing on both venues is placed in a tree of category, then event, then market, and two markets "
        "are compared only when they hang off the same event. A pair counts as exact only when both contracts are "
        "about the same event and outcome, with the same decision date and the same resolving authority. Fed rate "
        "brackets are judged by a fixed rule, every other candidate goes to an LLM, and a person checks a sample "
        "of the verdicts at a review sitting."),
    "edge": (
        "Prices come from our own snapshots of both venues' order books, taken every 60 seconds, not from last "
        "trades or midpoints. For each exact pair we buy Yes on one venue and No on the other, walking up both "
        "venues' lists of offers slice by slice and paying each venue's taker fee, so the edge is what is left per "
        "$1 contract after the cost of trading. An episode is a stretch where that net edge is at least 1¢, at "
        "least $100 can be filled, and both hold in at least 2 consecutive snapshots."),
    "crypto": (
        "Both venues list Bitcoin and Ethereum price ladders, but they settle on different price indexes, so only "
        "ladders that settle at the same instant are compared. A Kalshi strike against a different Polymarket "
        "strike counts only when the gap between the two strikes is larger than the 99.9th percentile of the "
        f"difference between the two settlement indexes over the past {BASIS_WINDOW_DAYS} days. Then at least one "
        "leg pays out in every state, as long as the indexes stay within that measured range."),
    "not_shown": (
        "The page does not show either venue's individual quotes or order books, because the venues' data terms "
        "do not permit displaying them publicly; it shows only numbers derived from them, such as net edge, fees "
        "and fillable size. Kalshi's taker fee coefficient is taken from its public documentation and has not been "
        "verified, so an edge could be off by up to about a cent per contract."),
}

HEADLINE: dict[str, str] = {
    "question": "Is there cross-venue arbitrage between Kalshi and Polymarket?",
    "answer": ("Across {pairs} exact pairs and {snapshots} order-book snapshots since {since}, the largest net edge "
               "after fees was {max_edge}; {episodes} episodes met the rule of at least 1¢ and $100 over two "
               "snapshots, and {us_episodes} of them could be executed from the US."),
    "ladders": ("On same-instant crypto ladders, {violations} of {checks} admissible cross-strike checks had a net "
                "edge of at least 1¢."),
}

NOT_COMPUTED = "research summary not computed yet"
