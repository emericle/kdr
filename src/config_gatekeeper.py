import importlib.util
import os
import sys
from types import ModuleType

# Ensure the project root is in sys.path when this module is run as a script
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.database import load_environment

# What it means for ``bin/check_env.py`` to be unloadable: the file is missing,
# does not parse, or its module-level form does not hold together. A handler
# names those, per the repo's rule that a catch names the failure it answers.
CHECK_ENV_FAILURES = (OSError, SyntaxError, ImportError, NameError, AttributeError, TypeError, ValueError)

# ``bin/check_env.py`` is the single source of truth for which environment
# variables are required. ``bin/`` is not a package, so load it explicitly from
# its path rather than restating the list here — restating it is exactly the
# drift this consolidation exists to prevent.
_CHECK_ENV_PATH = os.path.join(project_root, "bin", "check_env.py")
_check_env_module: ModuleType | None = None


def _load_check_env() -> ModuleType | None:
    """Load ``bin/check_env.py`` once and cache it, or ``None`` if it cannot load."""
    global _check_env_module
    if _check_env_module is not None:
        return _check_env_module
    if not os.path.isfile(_CHECK_ENV_PATH):
        return None
    spec = importlib.util.spec_from_file_location("kdr_check_env", _CHECK_ENV_PATH)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except CHECK_ENV_FAILURES:
        return None
    _check_env_module = module
    return module


def validate_configs(exit_on_error: bool = True) -> bool:
    """Checks for necessary environment variables.

    Reads ``.env`` first: the credentials live there, and nothing else in
    ``src/`` loads it at import any more, so this gate is the one place that
    decides whether the process has a configuration at all. The list of
    required variables is read from ``bin/check_env.py`` rather than restated
    here, so the two cannot drift apart.
    """
    load_environment()

    check_env = _load_check_env()
    if check_env is None:
        msg = (
            "Error: cannot load bin/check_env.py, the single source of truth "
            "for required environment variables. Fix it before starting."
        )
        if exit_on_error:
            print(msg)
            sys.exit(1)
        return False

    missing = check_env.missing_keys()

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
