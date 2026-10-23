"""Schwab Market Data (OAuth, REST): stock quotes and daily history for the stock-perp reference.

Market Data product only; no account or order endpoints. The refresh token expires 7 days after login, so
`python -m venue_arb.sources.schwab login` re-issues it into Bitwarden. Errors name the path only, never the query,
and nothing here prints a secret value."""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
API = "https://api.schwabapi.com/marketdata/v1"
REFRESH_TTL = timedelta(days=7)
ACCESS_TTL_S = 1800
QUOTE_BATCH = 100  # symbols per `quotes` call
CALLS_PER_MIN = 120
REDIRECT_URI = "https://127.0.0.1"
SERVICE = "venue-arb-perps"


class SchwabAuthError(RuntimeError):
    """HTTP 401 from a data endpoint, or any 4xx from the token endpoint: the refresh token is expired or revoked.
    Schwab most likely answers an expired refresh token with 400 `refresh_token_authentication_error` (schwab-py;
    unverified, as recalled)."""


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    bid_size: float  # as Schwab reports it; may be round lots for some symbols (docs/venues.md)
    ask_size: float
    last: float | None
    quote_time: datetime


def refresh_expires_at(issued_at: datetime) -> datetime:
    return issued_at + REFRESH_TTL


def _ms(dt: datetime) -> int:
    return round(dt.timestamp() * 1000)


def _checked(r, url: str):
    path = urlparse(url).path
    if r.status_code == 401 or (url == TOKEN_URL and 400 <= r.status_code < 500):
        raise SchwabAuthError(f"{path} returned HTTP {r.status_code}: Schwab login expired, re-run the login CLI")
    if r.status_code >= 400:
        raise RuntimeError(f"{path} failed: HTTP {r.status_code}")
    return r.json()


class Client:
    def __init__(self, client_id: str, secret: str, refresh_token: str, post=httpx.post, get=httpx.get,
                 clock=time.monotonic):
        self._auth, self._refresh = (client_id, secret), refresh_token
        self._post, self._get, self._clock = post, get, clock
        self._token, self._valid_until = "", float("-inf")

    def access_token(self) -> str:
        if self._clock() >= self._valid_until:
            r = self._post(TOKEN_URL, auth=self._auth, timeout=30,
                           data={"grant_type": "refresh_token", "refresh_token": self._refresh})
            body = _checked(r, TOKEN_URL)
            self._token = body["access_token"]
            self._valid_until = self._clock() + body.get("expires_in", ACCESS_TTL_S) - 60
        return self._token

    def _data(self, endpoint: str, params: dict):
        url = f"{API}/{endpoint}?{urlencode(params)}"
        return _checked(self._get(url, headers={"Authorization": f"Bearer {self.access_token()}"}, timeout=30), url)

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out = {}
        for i in range(0, len(symbols), QUOTE_BATCH):
            body = self._data("quotes", {"symbols": ",".join(symbols[i : i + QUOTE_BATCH]), "fields": "quote"})
            for sym, v in body.items():
                q = v.get("quote") if isinstance(v, dict) else None
                if not q or "bidPrice" not in q or "askPrice" not in q or "quoteTime" not in q:
                    continue  # invalid symbols arrive under `errors`; the call still succeeds
                last = q.get("lastPrice")
                out[sym] = Quote(sym, float(q["bidPrice"]), float(q["askPrice"]), float(q.get("bidSize", 0)),
                                 float(q.get("askSize", 0)), None if last is None else float(last),
                                 datetime.fromtimestamp(q["quoteTime"] / 1000, UTC))
        return out

    def price_history(self, symbol: str, start: datetime, end: datetime) -> list[dict]:
        body = self._data("pricehistory", {"symbol": symbol, "periodType": "month", "frequencyType": "daily",
                                           "frequency": 1, "startDate": _ms(start), "endDate": _ms(end)})
        return [{"day": datetime.fromtimestamp(c["datetime"] / 1000, UTC).date(),
                 "open": float(c["open"]), "close": float(c["close"])} for c in body.get("candles", [])]


def exchange_code(client_id: str, secret: str, redirect_url: str, redirect_uri: str, post=httpx.post) -> dict:
    """Trade the pasted redirect URL's `code` (parse_qs URL-decodes it, which Schwab requires) for tokens."""
    codes = parse_qs(urlparse(redirect_url.strip()).query).get("code")
    if not codes:
        raise ValueError("no `code` parameter in the pasted redirect URL")
    r = post(TOKEN_URL, auth=(client_id, secret), timeout=30,
             data={"grant_type": "authorization_code", "code": codes[0], "redirect_uri": redirect_uri})
    return _checked(r, TOKEN_URL)


def _bws(args: list[str], env) -> str:
    # The value rides as an argument (the bws CLI's own documented form); stdout is captured so it never prints.
    r = subprocess.run(["bws", *args], env=dict(env), capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"bws secret {args[1]} failed (exit {r.returncode})")
    return r.stdout


def _write_secrets(values: dict[str, str], env) -> None:
    project = env["BWS_PROJECT_ID"]
    ids = {s["key"]: s["id"] for s in json.loads(_bws(["secret", "list", "--output", "json", project], env) or "[]")}
    for name, value in values.items():
        if name in ids:
            _bws(["secret", "edit", ids[name], "--value", value], env)
        else:
            _bws(["secret", "create", name, value, project], env)
        print(f"wrote {name}")


def main(argv=None, stdin=sys.stdin, env=os.environ) -> int:
    p = argparse.ArgumentParser(prog="python -m venue_arb.sources.schwab")
    p.add_argument("command", choices=["login"])
    p.add_argument("--no-restart", action="store_true", help=f"skip `systemctl --user restart {SERVICE}`")
    args = p.parse_args(argv)
    cid, secret = env["SCHWAB_CLIENT_ID"], env["SCHWAB_CLIENT_SECRET"]
    print(f"Open: {AUTH_URL}?{urlencode({'response_type': 'code', 'client_id': cid, 'redirect_uri': REDIRECT_URI})}")
    print("Sign in, approve, then paste the full URL the browser lands on (the page will not load):")
    try:
        tokens = exchange_code(cid, secret, stdin.readline(), REDIRECT_URI)
        _write_secrets({"SCHWAB_REFRESH_TOKEN": tokens["refresh_token"],
                        "SCHWAB_REFRESH_ISSUED_AT": datetime.now(UTC).isoformat(timespec="seconds")}, env)
    except (SchwabAuthError, ValueError, RuntimeError, KeyError) as e:
        print(f"login failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if not args.no_restart:
        r = subprocess.run(["systemctl", "--user", "restart", SERVICE], check=False)
        print(f"restart {SERVICE}: {'ok' if r.returncode == 0 else f'failed (exit {r.returncode})'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
