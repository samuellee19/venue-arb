"""The words the note and the dashboard's Research tab share. Pure: no qa, no I/O.

METHOD is shipped in the bundle by the publisher; `note.render` uses `edge` and `crypto` verbatim in place of its
equivalent method sentences (`matching` and `not_shown` have no equivalent in the note), so both say the same thing.
HEADLINE holds str.format templates the reducer (`venue_arb.report.research`) fills.
"""
from venue_arb.config import BASIS_WINDOW_DAYS

METHOD: dict[str, str] = {
    "matching": (
        "Candidate pairs are proposed within a category, by a deterministic rule for Fed rate brackets and by "
        "embedding similarity of the market text for everything else. Each candidate is then judged, Fed brackets "
        "by that fixed rule and every other pair by a language model, on whether both markets resolve on the same "
        "event, outcome, decision date and authority. A sample of verdicts is checked by hand at review sittings; "
        "the latest sitting's precision is shown with these figures."),
    "edge": (
        "Prices come from our own snapshots of both venues' order books, taken every 60 seconds, not from last "
        "trades or midpoints. For each exact pair we buy Yes on one venue and No on the other, walking up both "
        "venues' lists of offers slice by slice and paying each venue's taker fee, so the edge is what is left per "
        "$1 contract after the cost of trading. An episode is a stretch where that net edge is at least 1¢, at "
        "least $100 can be filled, and both hold in at least 2 consecutive snapshots."),
    "crypto": (
        "Both venues list Bitcoin and Ethereum price ladders, but they settle on different price indexes, so only "
        "ladders that settle at the same instant are compared. A Kalshi strike against a different Polymarket "
        "strike counts only when the gap between the two strikes is at least the 99.9th percentile of the "
        f"difference between the two settlement indexes over the past {BASIS_WINDOW_DAYS} days. Then at least one "
        "leg pays out in every state, as long as the indexes stay within that measured range."),
    "not_shown": (
        "The page does not show either venue's quotes or order books, only numbers derived from them, such as net "
        "edge, fees and fillable size. Kalshi's data terms do not permit public display of its prices without "
        "written permission, and Polymarket's terms could not be read, so quotes from both venues are withheld until "
        "that is settled. Kalshi's taker fee coefficient is secondhand and unverified, because its fee schedule could "
        "not be fetched, and Polymarket's fee rounding rule is unverified, so an edge could be off by up to about a "
        "cent per contract."),
}

HEADLINE: dict[str, str] = {
    "question": "Is there cross-venue arbitrage between Kalshi and Polymarket?",
    "answer": ("Across {pairs} and {snapshots} since {since}, the largest net edge "
               "after fees was {max_edge}; {episodes} met the rule of at least 1¢ and $100 over two "
               "snapshots, and {us_episodes} of them could be executed from the US."),
    "ladders": ("On same-instant crypto ladders, {violations} of {checks} admissible cross-strike checks had a net "
                "edge of at least 1¢."),
    "ladders_none": ("On same-instant crypto ladders there are no counted hours yet, so no admissible checks to "
                     "report."),
}

NOT_COMPUTED = "research summary not computed yet"
