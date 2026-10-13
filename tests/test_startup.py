#!/usr/bin/env python3
"""
Test script to verify environment variable checking logic and system setup.
This simulates what the startup script does for environment validation.
"""

import os
import sys
from pathlib import Path

# Resolve project root directory regardless of where script is invoked
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name in ("tests", "test") else SCRIPT_DIR
if not (PROJECT_ROOT / "requirements.txt").exists():
    PROJECT_ROOT = Path.cwd()

# Ensure project root is in sys.path
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_required_env_vars():
    """Test that all required environment variables are present."""
    required_keys = [
        "ADANOS_API_KEY",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "DATABASE_URL",
    ]

    missing = [key for key in required_keys if not os.getenv(key)]
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        raise AssertionError(f"Missing environment variables: {', '.join(missing)}")
    print("✅ All required environment variables are present")


def test_python_version():
    """Test that Python 3.13+ is available."""
    if sys.version_info < (3, 13):
        print(f"❌ Python version {sys.version} is too old. Requires 3.13+")
        raise AssertionError(f"Python version {sys.version} is too old. Requires 3.13+")
    print(f"✅ Python version {sys.version_info.major}.{sys.version_info.minor} is compatible")


def test_requirements_file():
    """Test that requirements.txt exists."""
    req_file = PROJECT_ROOT / "requirements.txt"
    try:
        with open(req_file) as f:
            deps = [line.strip() for line in f if line.strip()]
        print(f"✅ Found {len(deps)} dependencies in requirements.txt")
    except FileNotFoundError:
        print("❌ requirements.txt not found")
        raise AssertionError("requirements.txt not found") from None


def test_src_file():
    """Test that the main source file exists."""
    src_file = PROJECT_ROOT / "src" / "scraper.py"
    try:
        with open(src_file) as f:
            _ = f.read()
        print("✅ Main source file (src/scraper.py) exists")
    except FileNotFoundError:
        print("❌ Main source file (src/scraper.py) not found")
        raise AssertionError("Main source file (src/scraper.py) not found") from None


def test_imports():
    """Test that modules can be imported correctly."""
    try:
        from src.config_gatekeeper import validate_configs  # noqa: F401
        from src.database import DatabaseManager  # noqa: F401
        from src.state_mapper import StateMapper  # noqa: F401

        print("✅ All module imports work correctly")
    except ImportError as e:
        print(f"❌ Module import failed: {e}")
        print("   Make sure PYTHONPATH includes the project root directory")
        raise AssertionError(f"Module import failed: {e}") from e


def main():
    """Run all tests."""
    print("=" * 50)
    print("KDR Startup Validation Tests")
    print("=" * 50)
    print()

    tests = [
        ("Python Version", test_python_version),
        ("Environment Variables", test_required_env_vars),
        ("Requirements File", test_requirements_file),
        ("Source File", test_src_file),
        ("Module Imports", test_imports),
    ]

    results = {}
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        try:
            test_func()
            results[test_name] = True
        except AssertionError:
            results[test_name] = False
        except Exception as e:
            print(f"❌ Error running test: {e}")
            results[test_name] = False

    print()
    print("=" * 50)
    print("Summary:")
    print("=" * 50)

    passed = sum(results.values())
    total = len(results)

    for test_name, passed_flag in results.items():
        status = "✅ PASS" if passed_flag else "❌ FAIL"
        print(f"{test_name}: {status}")

    print()
    if passed == total:
        print("🎉 All tests passed! You can proceed with startup.")
        return 0
    print(f"⚠️  {passed}/{total} tests passed. Check the failures above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
