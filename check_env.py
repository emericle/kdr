#!/usr/bin/env python3
"""
Environment variable checker utility.
Shows the status of all required environment variables.
"""

import os

def check_environment():
    """Check and display all required environment variables."""
    print("=" * 60)
    print("KDR Environment Variable Status")
    print("=" * 60)
    print()

    required_vars = {
        "ADANOS_API_KEY": "Adanos AI API Key",
        "ALPACA_API_KEY": "Alpaca API Key",
        "ALPACA_SECRET_KEY": "Alpaca Secret Key",
        "DATABASE_URL": "PostgreSQL Database URL"
    }

    all_present = True
    for key, description in required_vars.items():
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
    import sys
    success = check_environment()
    sys.exit(0 if success else 1)