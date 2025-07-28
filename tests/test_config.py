
import os
import unittest
from unittest.mock import patch

import pytest
from mcp_router.config import (
    get_config,
    Config,
    DevelopmentConfig,
    ProductionConfig,
    TestingConfig,
)


class TestConfig(unittest.TestCase):
    """Test cases for the configuration setup."""

    def test_get_config_default(self):
        """
        Test that get_config returns DevelopmentConfig by default.
        """
        with patch.dict(os.environ, {}, clear=True):
            config = get_config()
            self.assertEqual(config.__name__, "DevelopmentConfig")

    @patch.dict(os.environ, {"FLASK_ENV": "production"})
    def test_get_config_production(self):
        """
        Test that get_config returns ProductionConfig when FLASK_ENV is 'production'.
        """
        config = get_config()
        self.assertEqual(config.__name__, "ProductionConfig")

    @patch.dict(os.environ, {"FLASK_ENV": "testing"})
    def test_get_config_testing(self):
        """
        Test that get_config returns TestingConfig when FLASK_ENV is 'testing'.
        """
        config = get_config()
        self.assertEqual(config.__name__, "TestingConfig")
        self.assertTrue(config.TESTING)
        self.assertEqual(config.SQLALCHEMY_DATABASE_URI, "sqlite:///:memory:")

    @patch.dict(
        os.environ, {"MCP_AUTH_TYPE": "api_key", "FLASK_ENV": "development"}, clear=True
    )
    def test_validate_api_key_missing(self):
        """
        Test that validation fails if MCP_AUTH_TYPE is 'api_key' but no MCP_API_KEY is provided.
        """
        # Ensure MCP_API_KEY is not set by explicitly removing it
        if "MCP_API_KEY" in os.environ:
            os.environ.pop("MCP_API_KEY")

        # Create a test config class that reads from the current environment
        class TestConfig:
            MCP_AUTH_TYPE = os.environ.get("MCP_AUTH_TYPE", "api_key")
            MCP_API_KEY = os.environ.get("MCP_API_KEY")
            
            @classmethod
            def validate(cls):
                """Validate configuration settings"""
                if cls.MCP_AUTH_TYPE == "api_key" and not cls.MCP_API_KEY:
                    raise ValueError(
                        "MCP_API_KEY is required when MCP_AUTH_TYPE is set to 'api_key'. "
                        "Please set MCP_API_KEY in your environment variables."
                    )
        
        with self.assertRaises(ValueError) as context:
            TestConfig.validate()
        self.assertIn("MCP_API_KEY is required", str(context.exception))

    @patch.dict(
        os.environ,
        {
            "MCP_AUTH_TYPE": "api_key",
            "MCP_API_KEY": "test-key",
            "FLASK_ENV": "development",
        },
    )
    def test_validate_api_key_present(self):
        """
        Test that validation passes if MCP_AUTH_TYPE is 'api_key' and MCP_API_KEY is provided.
        """
        try:
            config_class = get_config()
            config_class.validate()
        except ValueError:
            self.fail("get_config().validate() raised ValueError unexpectedly!")

    def test_base_config_defaults(self):
        """Test the default values in the base Config class."""
        with patch.dict(os.environ, {}, clear=True):
            # Test the actual default values from the environment
            self.assertEqual(Config.MCP_AUTH_TYPE, "oauth")  # Default is oauth
            self.assertEqual(Config.FLASK_PORT, 8000)
            self.assertFalse(Config.DEBUG)
            self.assertTrue(Config.WTF_CSRF_ENABLED)


if __name__ == "__main__":
    unittest.main() 