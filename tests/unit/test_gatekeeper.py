import unittest
from unittest.mock import MagicMock, patch
from src.config_gatekeeper import validate_configs
import os
import sys

# Mocking the external library to avoid ImportErrors during testing
sys.modules["alpaca_trade_api"] = MagicMock()
sys.modules["alpaca_trade_api.rest"] = MagicMock()
sys.modules["alpaca_trade_api.streaming"] = MagicMock()

class TestConfigGatekeeper(unittest.TestCase):
    def test_missing_keys_raises_sys_exit(self):
        for key in ["ADANOS_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "DATABASE_URL"]:
            if key in os.environ:
                del os.environ[key]
        
        with self.assertRaises(SystemExit):
            validate_configs()

    def test_valid_keys_success(self):
        os.environ["ADANOS_API_KEY"] = "test"
        os.environ["ALPACA_API_KEY"] = "test"
        os.environ["ALPACA_SECRET_KEY"] = "test"
        os.environ["DATABASE_URL"] = "postgresql://user:pass@host/db"
        
        try:
            validate_configs()
        except SystemExit:
            self.fail("validate_configs raised SystemExit unexpectedly with valid keys")

if __name__ == "__main__":
    unittest.main()
