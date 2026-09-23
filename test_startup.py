#!/usr/bin/env python3
"""
Test script to verify environment variable checking logic.
This simulates what the startup script does for environment validation.
"""

import os
import sys

def test_required_env_vars():
    """Test that all required environment variables are present."""
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
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        return False
    else:
        print("✅ All required environment variables are present")
        return True

def test_python_version():
    """Test that Python 3.14+ is available."""
    if sys.version_info < (3, 14):
        print(f"❌ Python version {sys.version} is too old. Requires 3.14+")
        return False
    else:
        print(f"✅ Python version {sys.version_info.major}.{sys.version_info.minor} is compatible")
        return True

def test_requirements_file():
    """Test that requirements.txt exists."""
    try:
        with open("requirements.txt", "r") as f:
            deps = [line.strip() for line in f if line.strip()]
        print(f"✅ Found {len(deps)} dependencies in requirements.txt")
        return True
    except FileNotFoundError:
        print("❌ requirements.txt not found")
        return False

def test_src_file():
    """Test that the main source file exists."""
    try:
        with open("src/scraper.py", "r") as f:
            content = f.read()
        print("✅ Main source file (src/scraper.py) exists")
        return True
    except FileNotFoundError:
        print("❌ Main source file (src/scraper.py) not found")
        return False

def test_imports():
    """Test that modules can be imported correctly."""
    try:
        # Need to add current directory to path for imports
        import sys
        sys.path.insert(0, os.getcwd())

        from src.database import DatabaseManager
        from src.config_gatekeeper import validate_configs
        from src.state_mapper import StateMapper

        print("✅ All module imports work correctly")
        return True
    except ImportError as e:
        print(f"❌ Module import failed: {e}")
        print("   Make sure PYTHONPATH includes the project root directory")
        return False

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
    ]

    results = {}
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        try:
            results[test_name] = test_func()
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
    else:
        print(f"⚠️  {passed}/{total} tests passed. Check the failures above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())