import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
UNITS = sorted((ROOT / "deploy").glob("*.service"))


def unit_env(path: Path) -> dict[str, str]:
    lines = [ln.removeprefix("Environment=") for ln in path.read_text().splitlines() if ln.startswith("Environment=")]
    return dict(ln.split("=", 1) for ln in lines)


@pytest.mark.parametrize("unit", UNITS, ids=lambda p: p.name)
def test_unit_env_matches_mise(unit):
    env = unit_env(unit)
    mise = tomllib.loads((ROOT / "mise.toml").read_text())["env"]
    assert env["PGUSER"] == "venue_arb" and env["CLICKHOUSE_USER"] == "venue_arb"
    assert env["PREFECT_API_URL"]
    assert env["BWS_PROJECT_ID"] == mise["BWS_PROJECT_ID"]
    for k, v in mise.items():
        if k not in ("BWS_PROJECT_ID", "PYTHONPATH"):
            assert env.get(k) == v, k


def test_units_present():
    assert [p.name for p in UNITS] == ["venue-arb-collector.service", "venue-arb-flows.service", "venue-arb-perps.service", "venue-arb-publish.service"]
    assert not list((ROOT / "deploy").glob("*.timer"))


def test_unit_matches_collector_env():
    assert unit_env(ROOT / "deploy" / "venue-arb-perps.service") == unit_env(ROOT / "deploy" / "venue-arb-collector.service")


def test_server_unit_has_no_db_env():
    text = (ROOT / "deploy" / "server" / "venue-arb-web.service").read_text()
    env = unit_env(ROOT / "deploy" / "server" / "venue-arb-web.service")
    assert env["BUNDLE_DIR"] == "/srv/venue-arb/bundle"
    assert not [k for k in env if k.startswith(("PG", "CLICKHOUSE_", "BWS_", "PREFECT_"))]
    assert "User=venue-arb-web" in text and "ProtectSystem=strict" in text  # not the rsync push user
