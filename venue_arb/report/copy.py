"""The words the note and the dashboard's Research tab share. Pure: no qa, no I/O.

METHOD is shipped in the bundle by the publisher; `note.render` uses `edge` and `crypto` verbatim in place of its
equivalent method sentences (`matching` and `not_shown` have no equivalent in the note), so both say the same thing.
HEADLINE holds str.format templates the reducer (`venue_arb.report.research`) fills.
"""
from venue_arb.basis import BASIS_Q
from venue_arb.config import BASIS_WINDOW_DAYS

PERCENTILE = f"{100 * BASIS_Q:g}th percentile"  # 0.999 -> "99.9th percentile"

METHOD: dict[str, str] = {
    "matching": (
        "Candidate pairs are proposed within a category, by a deterministic rule for Fed rate brackets and by "
        "embedding similarity of the market text for everything else. Each candidate is then judged, Fed brackets "
        "by that fixed rule and every other pair by a language model, on whether both markets resolve on the same "
        "event, outcome, decision date and authority. Crypto price ladders are matched by a deterministic parser of "
        "the asset, strike and settlement instant, not by the language model. A sample of verdicts is checked by "
        "hand at review sittings; the precision shown with these figures covers every review of a current verdict."),
    "edge": (
        "Prices come from our own snapshots of both venues' order books, taken every 60 seconds, not from last "
        "trades or midpoints. For each exact pair we buy Yes on one venue and No on the other, walking up both "
        "venues' lists of offers slice by slice and paying each venue's taker fee, so the edge is what is left per "
        "$1 contract after the cost of trading. An episode is a stretch where that net edge is at least 1¢, at "
        "least $100 can be filled, and both hold in at least 2 consecutive snapshots."),
    "crypto": (
        "Both venues list Bitcoin and Ethereum price ladders, but they settle on different price indexes, so only "
        "ladders that settle at the same instant are compared. A Kalshi strike against a different Polymarket "
        f"strike counts only when the gap between the two strikes is at least the {PERCENTILE} of the "
        f"difference between the two settlement indexes over the past {BASIS_WINDOW_DAYS} days. Then at least one "
        "leg pays out in every state, as long as the indexes stay within that measured range."),
    "not_shown": (
        "The page does not show either venue's quotes or order books, only numbers derived from them, such as net "
        "edge, fees and fillable size. Kalshi's data terms do not permit public display of its prices without "
        "written permission, and Polymarket's terms could not be read, so quotes from both venues are withheld until "
        "that is settled. Kalshi's taker fee coefficient is secondhand and unverified, because its fee schedule could "
        "not be fetched, and Polymarket's fee rounding rule is unverified, so an edge could be off by up to about a "
        "cent per contract."),
    "stock_perps": (
        "A stock perp is a perpetual future on one US-listed (or Korean) share, traded on Hyperliquid's trade.xyz "
        "markets. The reference is the venue's own oracle: while the stock's market is open it is an external feed "
        "that follows the stock, and when the market is closed it follows the perp's own smoothed price, so the row "
        "reads oracle internal and is never an edge. The basis is the perp's price against the oracle in basis points "
        "(bps, hundredths of a percent), and the net is what is left after a round trip held 24 hours: the perp's "
        "taker fee twice, half the perp's bid-ask spread on exit, and funding for the hold. The stock leg is not "
        "modelled, so its spread and borrow come off the net before any trade. The weekend test compares the perp's "
        "price at Friday 8 PM, Saturday noon and Sunday 6 PM New York time with the oracle print at Monday's open. "
        "Hyperliquid blocks US traders, so none of this can be executed from the US."),
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
    "ladder_episodes": " On ladders, {episodes} met the same episode rule.",  # appended when there are any
    "stock_perps": ("Across {names} and {snapshots} since {since}, the largest net basis after round-trip costs and a "
                    "24 h hold was {max_net}; {episodes} met the rule of at least 10 bps and $100 over two snapshots."),
}
