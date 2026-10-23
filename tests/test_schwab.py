import io
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from venue_arb.sources import schwab

FX = Path(__file__).parent / "fixtures" / "schwab"


@pytest.fixture
def fx():
    return {p.stem: json.loads(p.read_text()) for p in FX.glob("*.json")}


def _resp(body, status=200):
    return SimpleNamespace(status_code=status, json=lambda: body)


def test_access_token_cached_until_sixty_seconds_before_expiry(fx):
    calls = []
    clock = [0.0]
    c = schwab.Client("id", "sec", "r", post=lambda *a, **k: calls.append(k) or _resp(fx["token"]), clock=lambda: clock[0])
    assert c.access_token() == "a" and c.access_token() == "a" and len(calls) == 1
    assert calls[0]["data"] == {"grant_type": "refresh_token", "refresh_token": "r"} and "r" not in str(calls[0].get("headers"))
    assert calls[0]["auth"] == ("id", "sec")
    clock[0] = 1800 - 59
    c.access_token()
    assert len(calls) == 2


def test_quotes_batched_and_parsed(fx):
    urls, heads = [], []
    c = schwab.Client("id", "sec", "r", post=lambda *a, **k: _resp(fx["token"]),
                      get=lambda url, **k: urls.append(url) or heads.append(k["headers"]) or _resp(fx["quotes"]))
    q = c.quotes([f"S{i}" for i in range(150)])
    assert len(urls) == 2
    assert "symbols=S0%2CS1" in urls[0] or "symbols=S0,S1" in urls[0]
    syms = [parse_qs(urlparse(u).query)["symbols"][0].split(",") for u in urls]
    assert [len(x) for x in syms] == [100, 50] and syms[1][0] == "S100"
    assert "fields=quote" in urls[0] and heads[0] == {"Authorization": "Bearer a"}
    assert q["NVDA"] == schwab.Quote("NVDA", 212.83, 212.85, 200.0, 300.0, 212.84, datetime(2026, 10, 22, 17, 41, 13, tzinfo=UTC))
    assert set(q) == {"NVDA", "AAPL"}  # the `errors` entry for an invalid symbol is skipped


def test_401_raises_auth_error(fx):
    c = schwab.Client("id", "sec", "r", post=lambda *a, **k: _resp(fx["token"]), get=lambda url, **k: _resp({}, 401))
    with pytest.raises(schwab.SchwabAuthError) as e:
        c.quotes(["NVDA"])
    assert "NVDA" not in str(e.value) and "/marketdata/v1/quotes" in str(e.value)
    c = schwab.Client("id", "sec", "r", post=lambda *a, **k: _resp({}, 401))
    with pytest.raises(schwab.SchwabAuthError):
        c.access_token()


def test_token_endpoint_4xx_is_an_auth_error_and_names_no_token(fx):
    # an expired refresh token most likely comes back 400 refresh_token_authentication_error (schwab-py; unverified)
    for status in (400, 403):
        c = schwab.Client("id", "sec", "r-secret", post=lambda *a, s=status, **k: _resp({}, s))
        with pytest.raises(schwab.SchwabAuthError) as e:
            c.access_token()
        assert "r-secret" not in str(e.value) and "/v1/oauth/token" in str(e.value) and str(status) in str(e.value)
    c = schwab.Client("id", "sec", "r", post=lambda *a, **k: _resp({}, 500))
    with pytest.raises(RuntimeError) as e:
        c.access_token()
    assert not isinstance(e.value, schwab.SchwabAuthError)
    c = schwab.Client("id", "sec", "r", post=lambda *a, **k: _resp(fx["token"]), get=lambda url, **k: _resp({}, 400))
    with pytest.raises(RuntimeError) as e:
        c.quotes(["NVDA"])
    assert not isinstance(e.value, schwab.SchwabAuthError)  # a data-endpoint 400 is a bad request, not a login


def test_price_history_rows(fx):
    urls = []
    c = schwab.Client("id", "sec", "r", post=lambda *a, **k: _resp(fx["token"]),
                      get=lambda url, **k: urls.append(url) or _resp(fx["pricehistory"]))
    rows = c.price_history("NVDA", datetime(2026, 10, 20, tzinfo=UTC), datetime(2026, 10, 22, tzinfo=UTC))
    assert rows == [{"day": date(2026, 10, 21), "open": 210.0, "close": 212.0},
                    {"day": date(2026, 10, 22), "open": 212.5, "close": 213.25}]
    assert "symbol=NVDA" in urls[0] and "startDate=1792454400000" in urls[0] and "endDate=1792627200000" in urls[0]


def test_refresh_expires_at():
    assert schwab.refresh_expires_at(datetime(2026, 10, 22, tzinfo=UTC)) == datetime(2026, 10, 29, tzinfo=UTC)


def test_exchange_code_decodes_the_code(fx):
    calls = []
    out = schwab.exchange_code("id", "sec", "https://127.0.0.1/?code=abc%40def&session=1", "https://127.0.0.1",
                               post=lambda *a, **k: calls.append(k) or _resp(fx["token"]))
    assert out["refresh_token"] == "r"
    assert calls[0]["data"] == {"grant_type": "authorization_code", "code": "abc@def", "redirect_uri": "https://127.0.0.1"}
    with pytest.raises(ValueError):
        schwab.exchange_code("id", "sec", "https://127.0.0.1/?x=1", "https://127.0.0.1", post=lambda *a, **k: None)


def test_login_cli_writes_secrets_without_printing_values(fx, capsys, monkeypatch):
    ran = []
    monkeypatch.setattr(schwab, "exchange_code", lambda *a, **k: {"refresh_token": "SECRETVALUE", "access_token": "x"})
    monkeypatch.setattr(schwab.subprocess, "run", lambda cmd, **k: ran.append(cmd) or SimpleNamespace(returncode=0, stdout="[]"))
    schwab.main(["login", "--no-restart"], stdin=io.StringIO("https://127.0.0.1/?code=abc%40def&session=1\n"), env={"SCHWAB_CLIENT_ID": "id", "SCHWAB_CLIENT_SECRET": "s", "BWS_PROJECT_ID": "p", "BWS_ACCESS_TOKEN": "w"})
    out = capsys.readouterr().out
    assert "SECRETVALUE" not in out and "SCHWAB_REFRESH_TOKEN" in out and "SCHWAB_REFRESH_ISSUED_AT" in out
    assert any("bws" in c[0] for c in ran) and not any("systemctl" in c[0] for c in ran)
    creates = [c for c in ran if c[:3] == ["bws", "secret", "create"]]
    assert [c[3] for c in creates] == ["SCHWAB_REFRESH_TOKEN", "SCHWAB_REFRESH_ISSUED_AT"] and creates[0][4] == "SECRETVALUE"


def test_login_cli_edits_existing_secret_and_restarts(monkeypatch, capsys):
    ran = []
    listing = json.dumps([{"id": "id-1", "key": "SCHWAB_REFRESH_TOKEN"}])
    monkeypatch.setattr(schwab, "exchange_code", lambda *a, **k: {"refresh_token": "SECRETVALUE"})
    monkeypatch.setattr(schwab.subprocess, "run", lambda cmd, **k: ran.append(cmd) or SimpleNamespace(returncode=0, stdout=listing))
    schwab.main(["login"], stdin=io.StringIO("https://127.0.0.1/?code=c\n"), env={"SCHWAB_CLIENT_ID": "id", "SCHWAB_CLIENT_SECRET": "s", "BWS_PROJECT_ID": "p", "BWS_ACCESS_TOKEN": "w"})
    assert ["bws", "secret", "edit", "id-1", "--value", "SECRETVALUE"] in ran
    assert ["systemctl", "--user", "restart", "venue-arb-perps"] in ran
    assert "SECRETVALUE" not in capsys.readouterr().out


def test_login_cli_needs_a_write_token_before_the_browser_step(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(schwab.subprocess, "run", lambda cmd, **k: ran.append(cmd))
    rc = schwab.main(["login"], stdin=io.StringIO(""), env={"SCHWAB_CLIENT_ID": "id", "SCHWAB_CLIENT_SECRET": "s", "BWS_PROJECT_ID": "p"})
    out = capsys.readouterr()
    assert rc == 1 and ran == [] and "Open:" not in out.out and "BWS_WRITE_TOKEN" in out.err


def test_login_cli_hands_bws_the_write_token(monkeypatch):
    envs = []
    monkeypatch.setattr(schwab, "exchange_code", lambda *a, **k: {"refresh_token": "r"})
    monkeypatch.setattr(schwab.subprocess, "run", lambda cmd, **k: envs.append(k.get("env")) or SimpleNamespace(returncode=0, stdout="[]"))
    rc = schwab.main(["login", "--no-restart"], stdin=io.StringIO("https://127.0.0.1/?code=c\n"), env={"SCHWAB_CLIENT_ID": "id", "SCHWAB_CLIENT_SECRET": "s", "BWS_PROJECT_ID": "p", "BWS_WRITE_TOKEN": "w"})
    assert rc == 0 and envs and all(e["BWS_ACCESS_TOKEN"] == "w" for e in envs)
