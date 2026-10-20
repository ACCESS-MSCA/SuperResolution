import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from packages.ndi.configuration import configuration, prepare_environment


class ConfigurationTests(unittest.TestCase):
    def test_single_tcp_matches_launcher_and_preserves_other_settings(self):
        original = {"ndi": {"vendor": {"name": "test"}, "tcp": {"recv": {"enable": True}}}}
        actual = configuration(original, "single-tcp", "192.168.50.10")
        for protocol in ("rudp", "tcp", "unicast", "multicast"):
            self.assertFalse(actual["ndi"][protocol]["send"]["enable"])
        self.assertTrue(actual["ndi"]["tcp"]["recv"]["enable"])
        self.assertEqual(actual["ndi"]["adapters"]["allowed"], ["192.168.50.10"])
        self.assertNotIn("adapters", original["ndi"])

    def test_bad_policy_and_address_fail_instead_of_silent_fallback(self):
        with self.assertRaises(ValueError): configuration({}, "typo", "")
        for address in ("127.0.0.1", "0.0.0.0", "224.0.0.1", "not-an-ip", "::1"):
            with self.assertRaises(ValueError): configuration({}, "auto", address)

    def test_gui_environment_produces_private_sdk_file_not_only_log_label(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ndi-config.v1.json"
            source.write_text('{"ndi":{"groups":{"send":"fixture"}}}')
            with patch.dict(os.environ, {"NDI_CONFIG_DIR": directory, "NDI_TRANSPORT": "single-tcp", "NDI_BIND_ADDRESS": ""}):
                status = prepare_environment()
                generated = Path(os.environ["NDI_CONFIG_DIR"]) / "ndi-config.v1.json"
                self.assertNotEqual(generated, source)
                self.assertTrue(status["generated"])
                self.assertFalse(json.loads(generated.read_text())["ndi"]["rudp"]["send"]["enable"])
                self.assertEqual(json.loads(source.read_text()), {"ndi": {"groups": {"send": "fixture"}}})

    def test_unavailable_interface_is_not_accepted(self):
        with patch.dict(os.environ, {"NDI_BIND_ADDRESS": "192.0.2.254", "NDI_TRANSPORT": "single-tcp"}):
            with self.assertRaises(OSError): prepare_environment()

if __name__ == "__main__": unittest.main()
