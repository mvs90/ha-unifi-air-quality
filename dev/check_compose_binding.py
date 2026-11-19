"""Validate the effective Compose publish address without starting containers.

Usage: python dev/check_compose_binding.py /path/to/compose.json
Generate the input with ``docker compose -f dev/compose.yaml config --format json``.
Exit 0 confirms the expected loopback binding; a mismatch exits 1. Missing or
malformed configuration is an error, not a successful validation.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    """Require homeassistant's sole published port to be 127.0.0.1:8123/TCP."""
    config = json.loads(Path(sys.argv[1]).read_text())
    ports = config["services"]["homeassistant"]["ports"]
    if len(ports) != 1 or not (
        ports[0].get("host_ip") == "127.0.0.1"
        and ports[0].get("target") == 8123
        and str(ports[0].get("published")) == "8123"
        and ports[0].get("protocol", "tcp") == "tcp"
    ):
        print("Development Compose must publish only 127.0.0.1:8123/tcp")
        return 1
    print("Effective development publish address: 127.0.0.1:8123/tcp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
