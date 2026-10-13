import os
import sys

# Ensure the project root is in sys.path when this module is run as a script
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.database import load_environment


def validate_configs(exit_on_error: bool = True) -> bool:
    """Checks for necessary environment variables.

    Reads ``.env`` first: the credentials live there, and nothing else in
    ``src/`` loads it at import any more, so this gate is the one place that
    decides whether the process has a configuration at all.
    """
    load_environment()
    required_keys = [
        "ADANOS_API_KEY",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "DATABASE_URL"
    ]

    missing = []
    for key in required_keys:
        if not os.getenv(key):
            missing.append(key)

    if missing:
        msg = f"Error: Missing the following environment variables:\n - {', '.join(missing)}"
        if exit_on_error:
            print(msg)
            sys.exit(1)
        return False

    print("Configuration validated successfully.")
    return True

if __name__ == "__main__":
    validate_configs()
