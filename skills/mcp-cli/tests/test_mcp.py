import argparse
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "mcp.py"
SPEC = importlib.util.spec_from_file_location("mcp_cli", MODULE_PATH)
mcp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mcp)


class ApprovedUrlTests(unittest.TestCase):
    def test_accepts_kraken_endpoint(self):
        self.assertEqual(
            mcp.approved_url("https://devhub.kraken.zone/mcp/"),
            "https://devhub.kraken.zone/mcp",
        )

    def test_accepts_exact_linearb_endpoint(self):
        self.assertEqual(
            mcp.approved_url("https://mcp.linearb.io/mcp/"),
            "https://mcp.linearb.io/mcp",
        )

    def test_rejects_other_linearb_path(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            mcp.approved_url("https://mcp.linearb.io/other")

    def test_rejects_unapproved_host(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            mcp.approved_url("https://example.com/mcp")


class HeaderEnvTests(unittest.TestCase):
    def test_resolves_header_without_exposing_secret_in_mapping(self):
        mapping = mcp.parse_header_env("x-api-key=LINEARB_API_KEY")
        with patch.dict(os.environ, {"LINEARB_API_KEY": "secret"}, clear=False):
            self.assertEqual(mcp.resolve_headers([mapping]), {"x-api-key": "secret"})

    def test_rejects_transport_header_override(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            mcp.parse_header_env("Content-Type=LINEARB_API_KEY")

    def test_rejects_missing_environment_variable(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(mcp.MCPError, "LINEARB_API_KEY"):
                mcp.resolve_headers([("x-api-key", "LINEARB_API_KEY")])

    def test_rejects_newline_in_secret(self):
        with patch.dict(os.environ, {"LINEARB_API_KEY": "bad\nvalue"}, clear=False):
            with self.assertRaisesRegex(mcp.MCPError, "newline"):
                mcp.resolve_headers([("x-api-key", "LINEARB_API_KEY")])


if __name__ == "__main__":
    unittest.main()
