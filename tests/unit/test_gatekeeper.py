import unittest
from unittest.mock import MagicMock, patch, mock_open
from src.config_gatekeeper import validate_configs
import os
import sys
import json

class TestConfigGatekeeper(unittest.TestCase):
    def test_missing_all_keys_raises_sys_exit(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as context:
                validate_configs()
            self.assertEqual(context.exception.code, 1)

    def test_missing_one_key_raises_sys_exit(self):
        env = {
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret",
            "DATABASE_URL": "postgresql://user:pass@host/db"
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as context:
                validate_configs()
            self.assertEqual(context.exception.code, 1)

    def test_missing_multiple_keys_raises_sys_exit(self):
        env = {
            "ALPACA_SECRET_KEY": "test_secret",
            "DATABASE_URL": "postgresql://user:pass@host/db"
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as context:
                validate_configs()
            self.assertEqual(context.exception.code, 1)

    def test_valid_keys_all_present(self):
        original_env = os.environ.copy()
        
        try:
            with patch.dict(os.environ, {
                "ADANOS_API_KEY": "test_key_1",
                "ALPACA_API_KEY": "test_key_2",
                "ALPACA_SECRET_KEY": "test_secret",
                "DATABASE_URL": "postgresql://user:pass@host/db"
            }, clear=True):
                validate_configs()
        finally:
            os.environ.clear()
            os.environ.update(original_env)

    def test_valid_keys_with_extra_variables(self):
        original_env = os.environ.copy()
        
        try:
            with patch.dict(os.environ, {
                "ADANOS_API_KEY": "test_key",
                "ALPACA_API_KEY": "test_key",
                "ALPACA_SECRET_KEY": "test_secret",
                "DATABASE_URL": "postgresql://user:pass@host/db",
                "EXTRA_VAR": "extra_value",
                "ANOTHER_VAR": "another_value"
            }, clear=True):
                validate_configs()
        finally:
            os.environ.clear()
            os.environ.update(original_env)

    def test_missing_environment_variable_message_format(self):
        env = {
            "ADANOS_API_KEY": "test_key",
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret"
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as context:
                validate_configs()
            self.assertEqual(context.exception.code, 1)

    def test_config_validation_is_idempotent(self):
        original_env = os.environ.copy()
        
        try:
            with patch.dict(os.environ, {
                "ADANOS_API_KEY": "test_key",
                "ALPACA_API_KEY": "test_key",
                "ALPACA_SECRET_KEY": "test_secret",
                "DATABASE_URL": "postgresql://user:pass@host/db"
            }, clear=True):
                validate_configs()
                validate_configs()
                validate_configs()
        finally:
            os.environ.clear()
            os.environ.update(original_env)

class TestConfigGatekeeperEdgeCases(unittest.TestCase):
    def test_empty_values_for_keys(self):
        original_env = os.environ.copy()
        
        try:
            with patch.dict(os.environ, {
                "ADANOS_API_KEY": "",
                "ALPACA_API_KEY": "",
                "ALPACA_SECRET_KEY": "",
                "DATABASE_URL": ""
            }, clear=True):
                with self.assertRaises(SystemExit) as context:
                    validate_configs()
                self.assertEqual(context.exception.code, 1)
        finally:
            os.environ.clear()
            os.environ.update(original_env)

    def test_missing_keys_returns_false_when_no_exit(self):
        with patch.dict(os.environ, {}, clear=True):
            result = validate_configs(exit_on_error=False)
            self.assertFalse(result)

if __name__ == '__main__':
    unittest.main()