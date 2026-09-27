"""CI-callable guardrail scripts. Each module exposes a `main()` invoked identically
by pre-commit and by GitHub Actions (Plan 04 wires the callers; Plan 01/02 create the
importable, directly-testable modules).
"""
