"""Tests for data/capture/power.py and the watchdog's sleep-risk alarm.

Pins down the failure that cost 2.71 h of irreplaceable L1 capture on
2026-09-14/15: the host ran on battery, macOS entered Maintenance Sleep, and
`caffeinate -s` did nothing because its assertion is honored on AC power only.
Nothing in the system said so — `pmset -g assertions` still showed the
assertion held for the whole 27 h.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from data.capture import power
from data.capture.gap_ledger import GapLedger
from data.capture.watchdog import Watchdog

_PS_AC = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t85%; AC attached;\n"
_PS_BATT = (
    "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t73%; discharging;\n"
)

_CUSTOM_SLEEP_ENABLED = (
    "Battery Power:\n standby              1\n sleep                1\n disksleep            10\n"
    "AC Power:\n standby              1\n sleep                1\n disksleep            10\n"
)
_CUSTOM_BATTERY_SLEEP_DISABLED = (
    "Battery Power:\n standby              1\n disablesleep         1\n sleep                1\n"
    "AC Power:\n standby              1\n disablesleep         0\n sleep                1\n"
)
# The trap this parser must not fall into: `disablesleep 1` under AC Power
# only, which does NOT protect a battery-powered capture run.
_CUSTOM_AC_ONLY_DISABLED = (
    "Battery Power:\n standby              1\n disablesleep         0\n sleep                1\n"
    "AC Power:\n standby              1\n disablesleep         1\n sleep                1\n"
)


def _runner_for(outputs: dict[str, str], returncode: int = 0):
    """Build a fake subprocess.run keyed by the pmset subcommand."""

    def _run(argv, **_kwargs):
        key = argv[-1]  # "ps" or "custom"
        return SimpleNamespace(returncode=returncode, stdout=outputs.get(key, ""))

    return _run


@pytest.fixture(autouse=True)
def _force_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests describe macOS behavior; the module short-circuits to None
    elsewhere, which would make every assertion below vacuously pass on Linux CI."""
    monkeypatch.setattr(power.platform, "system", lambda: "Darwin")


def test_is_on_battery_true_when_pmset_reports_battery_power() -> None:
    assert power.is_on_battery(_runner_for({"ps": _PS_BATT})) is True


def test_is_on_battery_false_when_pmset_reports_ac_power() -> None:
    assert power.is_on_battery(_runner_for({"ps": _PS_AC})) is False


def test_is_on_battery_none_when_pmset_fails_or_is_unparseable() -> None:
    assert power.is_on_battery(_runner_for({"ps": _PS_AC}, returncode=1)) is None
    assert power.is_on_battery(_runner_for({"ps": "gibberish\n"})) is None

    def _boom(*_a, **_k):
        raise OSError("pmset not found")

    assert power.is_on_battery(_boom) is None


def test_battery_sleep_disabled_reads_the_battery_block_not_the_ac_block() -> None:
    assert (
        power.battery_sleep_disabled(
            _runner_for({"custom": _CUSTOM_BATTERY_SLEEP_DISABLED})
        )
        is True
    )
    assert (
        power.battery_sleep_disabled(_runner_for({"custom": _CUSTOM_AC_ONLY_DISABLED}))
        is False
    ), "disablesleep=1 under AC Power must not be read as battery protection"
    assert (
        power.battery_sleep_disabled(_runner_for({"custom": _CUSTOM_SLEEP_ENABLED}))
        is False
    ), "absent disablesleep means sleep is enabled"


def test_sleep_risk_reports_only_on_battery_without_disablesleep() -> None:
    risk = power.sleep_risk(
        _runner_for({"ps": _PS_BATT, "custom": _CUSTOM_SLEEP_ENABLED})
    )
    assert risk is not None
    assert "BATTERY" in risk
    assert "disablesleep" in risk

    assert (
        power.sleep_risk(_runner_for({"ps": _PS_AC, "custom": _CUSTOM_SLEEP_ENABLED}))
        is None
    ), "on AC there is no risk to report"
    assert (
        power.sleep_risk(
            _runner_for({"ps": _PS_BATT, "custom": _CUSTOM_BATTERY_SLEEP_DISABLED})
        )
        is None
    ), "on battery WITH disablesleep set there is no risk to report"


def test_real_host_call_does_not_raise_and_returns_a_valid_type() -> None:
    """The live call must be safe on whatever host runs the suite."""
    assert power.is_on_battery() in (True, False, None)
    assert power.battery_sleep_disabled() in (True, False, None)
    assert power.sleep_risk() is None or isinstance(power.sleep_risk(), str)


# --- Watchdog integration -------------------------------------------------


def _watchdog(tmp_path: Path) -> tuple[Watchdog, GapLedger]:
    now_ns = time.time_ns()
    ledger = GapLedger(tmp_path)
    wd = Watchdog(
        {"A": now_ns, "B": now_ns, "merged": now_ns},
        ledger,
        tmp_path,
        conn_ids=["A", "B"],
    )
    return wd, ledger


def test_watchdog_records_one_power_row_while_on_battery_then_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    from collections import namedtuple

    plenty = namedtuple("_D", ["total", "used", "free"])(10**12, 10**9, 9 * 10**11)
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: plenty)

    wd, ledger = _watchdog(tmp_path)

    monkeypatch.setattr(
        "data.capture.watchdog.sleep_risk", lambda: "host is on BATTERY power"
    )
    wd._tick()
    wd._tick()  # ongoing risk must not duplicate

    df = ledger.read_all()
    power_rows = df.filter(df["stream"] == "__power__")
    assert power_rows.height == 1
    assert power_rows.row(0, named=True)["cause"].startswith("sleep-risk:")

    # Plugged back in -> alarm clears; unplugged again -> a NEW row.
    monkeypatch.setattr("data.capture.watchdog.sleep_risk", lambda: None)
    wd._tick()
    assert ledger.read_all().filter(df["stream"] == "__power__").height == 1

    monkeypatch.setattr(
        "data.capture.watchdog.sleep_risk", lambda: "host is on BATTERY power"
    )
    wd._tick()
    df2 = ledger.read_all()
    assert df2.filter(df2["stream"] == "__power__").height == 2


def test_watchdog_records_nothing_when_on_ac(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    from collections import namedtuple

    plenty = namedtuple("_D", ["total", "used", "free"])(10**12, 10**9, 9 * 10**11)
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: plenty)
    monkeypatch.setattr("data.capture.watchdog.sleep_risk", lambda: None)

    wd, ledger = _watchdog(tmp_path)
    wd._tick()

    assert ledger.read_all().height == 0


def test_watchdog_survives_a_raising_sleep_risk_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A watchdog that dies is worse than one that cannot read the battery."""
    import shutil
    from collections import namedtuple

    plenty = namedtuple("_D", ["total", "used", "free"])(10**12, 10**9, 9 * 10**11)
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: plenty)

    def _boom():
        raise subprocess.SubprocessError("pmset exploded")

    monkeypatch.setattr("data.capture.watchdog.sleep_risk", _boom)

    wd, ledger = _watchdog(tmp_path)
    wd._tick()  # must not raise

    assert ledger.read_all().height == 0


# --- WR-11 (03-REVIEW.md): undeterminable power source is never silent ------

_BATT_LAPTOP = (
    " -InternalBattery-0 (id=1)\t80%; AC attached; not charging present: true\n"
)
_BATT_DESKTOP = "Now drawing from 'AC Power'\n"


def _runner_codes(outputs: dict[str, tuple[int, str]]):
    def _run(argv, **_kwargs):
        code, out = outputs.get(argv[-1], (1, ""))
        return SimpleNamespace(returncode=code, stdout=out)

    return _run


def test_sleep_risk_reports_when_pmset_ps_fails_on_a_battery_host() -> None:
    risk = power.sleep_risk(
        _runner_codes({"ps": (1, ""), "batt": (0, _BATT_LAPTOP), "custom": (0, "")})
    )
    assert risk is not None and "UNDETERMINABLE" in risk


def test_sleep_risk_reports_when_every_pmset_call_fails() -> None:
    """Cannot even tell whether a battery exists: that is not "checked and
    safe" either."""
    assert power.sleep_risk(_runner_codes({})) is not None


def test_sleep_risk_reports_an_unrecognised_power_source_string() -> None:
    risk = power.sleep_risk(
        _runner_codes(
            {"ps": (0, "Now drawing from 'UPS Power'\n"), "batt": (0, _BATT_LAPTOP)}
        )
    )
    assert risk is not None and "UNDETERMINABLE" in risk


def test_sleep_risk_silent_when_checked_and_there_is_no_battery() -> None:
    assert (
        power.sleep_risk(
            _runner_codes(
                {
                    "ps": (0, "Now drawing from 'UPS Power'\n"),
                    "batt": (0, _BATT_DESKTOP),
                }
            )
        )
        is None
    )


def test_sleep_risk_silent_on_non_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not applicable off macOS (pmset does not exist; the capture host is a
    Mac) -- documented in the docstring, not a silent 'did not ask'."""
    monkeypatch.setattr(power.platform, "system", lambda: "Linux")
    assert power.sleep_risk(_runner_codes({})) is None
