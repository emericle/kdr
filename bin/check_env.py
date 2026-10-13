#!/usr/bin/env python3
"""
Environment variable checker utility.

This file is the single source of truth for which environment variables the
system requires. ``src/config_gatekeeper.py`` and the shell entry points read
the list from here (via ``required_keys()`` / the ``--keys`` flag) rather than
restating it, so the list cannot drift apart. It deliberately imports nothing
from ``src/`` so it stays standalone.
"""

import os
import sys

# The one place that decides which environment variables are required. Everything
# else reads this list instead of restating it.
REQUIRED_VARS: dict[str, str] = {
    "ADANOS_API_KEY": "Adanos AI API Key",
    "ALPACA_API_KEY": "Alpaca API Key",
    "ALPACA_SECRET_KEY": "Alpaca Secret Key",
    "DATABASE_URL": "PostgreSQL Database URL",
}


def required_keys() -> list[str]:
    """The environment variables the system requires, in declaration order."""
    return list(REQUIRED_VARS)


def missing_keys() -> list[str]:
    """Required keys that are unset (or set empty) in the current environment."""
    return [key for key in REQUIRED_VARS if not os.getenv(key)]


def check_environment() -> bool:
    """Check and display all required environment variables."""
    print("=" * 60)
    print("KDR Environment Variable Status")
    print("=" * 60)
    print()

    all_present = True
    for key, description in REQUIRED_VARS.items():
        value = os.getenv(key)
        if value:
            # Mask sensitive values
            if key == "DATABASE_URL":
                if "@" in value:
                    parts = value.split("@")
                    value = f"{parts[0]}@{len(parts[1]) * '*'}"
            else:
                value = "***" if key != "DATABASE_URL" else value[:20] + "..." + value[-20:]

            print(f"✅ {key}")
            print(f"   Description: {description}")
            print(f"   Value: {value}")
        else:
            print(f"❌ {key}")
            print(f"   Description: {description}")
            print(f"   Status: NOT SET")
            all_present = False
        print()

    print("=" * 60)
    if all_present:
        print("✅ All environment variables are properly configured")
        print("=" * 60)
        return True
    else:
        print("❌ Some environment variables are missing")
        print("=" * 60)
        print()
        print("To fix this:")
        print("1. Copy .env.example to .env")
        print("2. Edit .env and fill in your actual credentials")
        return False


if __name__ == "__main__":
    # `--keys` lets the shell entry points read the required list without
    # restating it: one key per line, always exit 0.
    if "--keys" in sys.argv[1:]:
        for key in required_keys():
            print(key)
        sys.exit(0)

    success = check_environment()
    sys.exit(0 if success else 1)
