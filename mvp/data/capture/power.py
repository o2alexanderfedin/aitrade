"""Host power-source detection — the sleep risk `caffeinate` cannot cover.

WHY THIS EXISTS: the daemon spawns `caffeinate -i -s -w <pid>`, whose `-s`
flag creates a `PreventSystemSleep` IOKit assertion. That assertion is
honored by macOS **only while the host is on AC power**; on battery the
system still enters Deep Idle / Maintenance Sleep and the assertion is
silently inert — `pmset -g assertions` keeps reporting it as held the whole
time, which is exactly what makes the failure invisible.

Observed 2026-09-14/15 on this host, on battery:

    Sleep  Entering Sleep state due to 'Maintenance Sleep':
           TCPKeepAlive=active Using Batt (Charge:73%)

Three sleeps cost 2.71 h of irreplaceable L1 in a 69.5 h run (96.10 % uptime).
The gap ledger recorded every second of it, but only *after the fact* and with
no indication of the cause. This module lets the watchdog name the cause while
the risk is live, so an unplugged laptop is a loud, ledgered condition rather
than a silent one.

The OS-level fix is `sudo pmset -b disablesleep 1`; this module is the
detection that makes its absence visible.
"""

from __future__ import annotations

import platform
import subprocess

#: `pmset -g ps` prints one of these as the first line's quoted source name.
_AC_MARKERS = ("AC Power", "AC attached")


def is_on_battery(runner=subprocess.run) -> bool | None:
    """Return True if this host is running on battery, False if on AC, and
    None if the question does not apply or cannot be answered (non-macOS, no
    `pmset`, a desktop with no battery, or a failed/garbled call).

    None is deliberately distinct from False: "we could not tell" must never
    render as "we checked and it is fine".
    """
    if platform.system() != "Darwin":
        return None
    try:
        result = runner(
            ["pmset", "-g", "ps"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None

    first_line = result.stdout.splitlines()[0]
    if any(marker in first_line for marker in _AC_MARKERS):
        return False
    if "Battery Power" in first_line:
        return True
    return None


def battery_sleep_disabled(runner=subprocess.run) -> bool | None:
    """Return True if `pmset -b disablesleep 1` is in effect (battery sleep
    suppressed at OS level), False if it is not, None if undeterminable.

    When this is True, being on battery is no longer a capture risk, so the
    watchdog stays quiet.
    """
    if platform.system() != "Darwin":
        return None
    try:
        result = runner(
            ["pmset", "-g", "custom"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None

    in_battery_block = False
    for raw_line in result.stdout.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("Battery Power"):
            in_battery_block = True
            continue
        if stripped.startswith("AC Power"):
            in_battery_block = False
            continue
        if in_battery_block and stripped.startswith("disablesleep"):
            return stripped.split()[-1] == "1"
    return False


def sleep_risk(runner=subprocess.run) -> str | None:
    """Return a human-readable description of the host's sleep risk, or None
    when there is none to report.

    Risk exists only when the host is genuinely on battery AND battery sleep
    has not been disabled at OS level. An undeterminable power source is
    reported too — silence must mean "checked and safe", never "did not ask".
    """
    on_battery = is_on_battery(runner)
    if on_battery is False:
        return None
    if on_battery is None:
        return None  # no battery, or not macOS — nothing meaningful to warn about

    if battery_sleep_disabled(runner) is True:
        return None
    return (
        "host is on BATTERY power and `pmset -b disablesleep` is not set — "
        "macOS will enter Maintenance Sleep and stop capture despite "
        "caffeinate (whose -s assertion is honored on AC power only). "
        "Fix: plug in, or run `sudo pmset -b disablesleep 1`."
    )
