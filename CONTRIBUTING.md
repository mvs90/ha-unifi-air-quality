# Contributing

Bug reports, reviewed anonymized diagnostics, tests, and pull requests are
welcome. Never post credentials, cookies, tokens, public IP addresses, MAC
addresses, serial numbers, unredacted device IDs, or household/device names.

Run `pytest` and `ruff check .` before opening a pull request. Changes to the
private protocol adapter should include a sanitized fixture and focused tests.
Keep Home Assistant-specific logic outside `api.py` so the transport can later
be replaced by the official Public Integration API.
