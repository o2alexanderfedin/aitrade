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

WHERE macOS ACTUALLY REPORTS `disablesleep` (measured on this host
2026-09-17, with `pmset -b disablesleep 1` in effect): `pmset -g custom`
prints NO `disablesleep` line at all, in either the `Battery Power` or the
`AC Power` block. The setting surfaces only in `pmset -g live`, as a
`System-wide power settings:` section with ` SleepDisabled\t\t1`. The first
version of `battery_sleep_disabled` parsed `pmset -g custom` only, so it
could never return True: the startup SLEEP RISK warning and the watchdog's
`__power__` gap-ledger rows fired permanently and falsely, on a host where
the risk had actually been fixed -- a boy-who-cried-wolf failure in the one
alarm that exists to make an invisible data-loss condition visible.

`pmset -g live` is therefore the primary source, with the `pmset -g custom`
battery-block parse kept as a fallback for hosts/OS versions that do print
it there. The WR-11 contract is unchanged: None means "could not tell",
never "checked and it is off". If either command answered and neither names
the key, that IS an answer (the setting is not in effect) and the result is
False; None is returned only when no `pmset` call answered at all.
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


def _pmset(runner, subcommand: str) -> str | None:
    """`pmset -g <subcommand>`'s stdout, or None if it could not be asked or
    answered nothing usable (missing binary, non-zero exit, empty output)."""
    try:
        result = runner(
            ["pmset", "-g", subcommand], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _sleep_disabled_from_live(stdout: str) -> bool | None:
    """Read `SleepDisabled` out of `pmset -g live`. None when the key is
    absent (macOS omits it entirely rather than printing a 0)."""
    for raw_line in stdout.splitlines():
        fields = raw_line.split()
        if len(fields) >= 2 and fields[0] == "SleepDisabled":
            return fields[1] == "1"
    return None


def _disablesleep_from_custom(stdout: str) -> bool | None:
    """Read `disablesleep` out of the BATTERY block of `pmset -g custom`, on
    hosts/OS versions that print it there. None when the key is absent.

    The `AC Power` block's own `disablesleep` is deliberately ignored: it
    does not protect a battery-powered capture run.
    """
    in_battery_block = False
    for raw_line in stdout.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("Battery Power"):
            in_battery_block = True
            continue
        if stripped.startswith("AC Power"):
            in_battery_block = False
            continue
        if in_battery_block and stripped.startswith("disablesleep"):
            return stripped.split()[-1] == "1"
    return None


def battery_sleep_disabled(runner=subprocess.run) -> bool | None:
    """Return True if `pmset -b disablesleep 1` is in effect (battery sleep
    suppressed at OS level), False if it is not, None if undeterminable.

    `pmset -g live`'s `SleepDisabled` is the source of truth -- macOS does
    not print `disablesleep` in `pmset -g custom` at all on this host (see
    the module docstring). `pmset -g custom`'s battery block is kept as a
    fallback for hosts/OS versions that do.

    When this is True, being on battery is no longer a capture risk, so the
    watchdog stays quiet.
    """
    if platform.system() != "Darwin":
        return None

    live = _pmset(runner, "live")
    if live is not None:
        value = _sleep_disabled_from_live(live)
        if value is not None:
            return value

    custom = _pmset(runner, "custom")
    if custom is not None:
        value = _disablesleep_from_custom(custom)
        if value is not None:
            return value

    if live is None and custom is None:
        return None  # nothing answered: "could not tell", never "it is off"
    return False


def has_internal_battery(runner=subprocess.run) -> bool | None:
    """Return True if `pmset -g batt` lists an internal battery, False if it
    answered and lists none (a desktop Mac), None if it could not be asked
    or answered nothing usable."""
    if platform.system() != "Darwin":
        return None
    try:
        result = runner(
            ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return "InternalBattery" in result.stdout


def sleep_risk(runner=subprocess.run) -> str | None:
    """Return a human-readable description of the host's sleep risk, or None
    when there is none to report.

    Silence must mean "checked and safe", never "did not ask" (03-REVIEW.md
    WR-11: the power-source-undeterminable branch used to return None, which
    recreated the silently-inert failure this module exists to close). On
    macOS, None is returned only when:
    - the host is on AC power; or
    - it is on battery AND `pmset -b disablesleep 1` is in effect; or
    - the power source could not be read BUT `pmset -g batt` answered and
      lists no internal battery (a desktop, e.g. on a UPS).
    Every other case -- on battery with sleep enabled, or a power source that
    cannot be determined on a host that has (or may have) a battery -- returns
    a description.

    Off macOS this returns None: `pmset` does not exist there and the
    battery-sleep failure mode is macOS-specific (the capture host is a Mac;
    the Linux container deploy has no such risk to report).
    """
    if platform.system() != "Darwin":
        return None

    on_battery = is_on_battery(runner)
    if on_battery is False:
        return None
    if on_battery is None:
        if has_internal_battery(runner) is False:
            return None  # checked: no internal battery on this host
        return (
            "power source UNDETERMINABLE (`pmset -g ps` failed or printed an "
            "unrecognised source) on a host that has or may have a battery -- "
            "cannot rule out battery sleep stopping capture. Check "
            "`pmset -g ps`; fix: plug in, or `sudo pmset -b disablesleep 1`."
        )

    if battery_sleep_disabled(runner) is True:
        return None
    return (
        "host is on BATTERY power and `pmset -b disablesleep` is not set — "
        "macOS will enter Maintenance Sleep and stop capture despite "
        "caffeinate (whose -s assertion is honored on AC power only). "
        "Fix: plug in, or run `sudo pmset -b disablesleep 1`."
    )
