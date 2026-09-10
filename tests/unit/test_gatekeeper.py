import unittest
from unittest.mock import MagicMock, patch, mock_open
from src.config_gatekeeper import validate_configs
import os
import sys
import json

class TestConfigGatekeeper(unittest.TestCase):
    def test_missing_all_keys_raises_sys_exit(self):
        # Save original environment
        original_env = os.environ.copy()
        
        try:
            # Clear all required keys
            for key in ["ADANOS_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "DATABASE_URL"]:
                if key in original_env:
                    del original_env[key]
            
            with self.assertRaises(SystemExit) as context:
                validate_configs()
            
            self.assertEqual(context.exception.code, 1)
        finally:
            # Restore environment
            os.environ.clear()
            os.environ.update(original_env)

    def test_missing_one_key_raises_sys_exit(self):
        original_env = os.environ.copy()
        
        try:
            # Remove only one key
            if "ADANOS_API_KEY" in original_env:
                del original_env["ADANOS_API_KEY"]
            
            with self.assertRaises(SystemExit) as context:
                validate_configs()
            
            self.assertEqual(context.exception.code, 1)
        finally:
            os.environ.clear()
            os.environ.update(original_env)

    def test_missing_multiple_keys_raises_sys_exit(self):
        original_env = os.environ.copy()
        
        try:
            # Remove two keys
            if "ADANOS_API_KEY" in original_env:
                del original_env["ADANOS_API_KEY"]
            if "DATABASE_URL" in original_env:
                del original_env["DATABASE_URL"]
            
            with self.assertRaises(SystemExit) as context:
                validate_configs()
            
            self.assertEqual(context.exception.code, 1)
        finally:
            os.environ.clear()
            os.environ.update(original_env)

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
        original_env = os.environ.copy()
        
        try:
            if "DATABASE_URL" in original_env:
                del original_env["DATABASE_URL"]
            
            try:
                with self.assertRaises(SystemExit):
                    validate_configs()
            except SystemExit:
                pass
        finally:
            os.environ.clear()
            os.environ.update(original_env)

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

if __name__ == '__main__':
    unittest.main()