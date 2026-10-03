"""Validate Pipeline v2 defaults without capturing a screen or issuing input."""

import json
from pathlib import Path
import unittest

from maa.resource import Resource


RESOURCE_DIR = Path(__file__).resolve().parents[1] / "assets" / "resources"


class PipelineDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defaults = json.loads((RESOURCE_DIR / "default_pipeline.json").read_text(encoding="utf-8-sig"))
        cls.resource = Resource()
        if not cls.resource.post_bundle(RESOURCE_DIR).wait().succeeded:
            raise AssertionError("The project resource bundle failed to load")
        probes = {
            "UnitTest_Default_OCR": {"recognition": {"type": "OCR"}},
            "UnitTest_Default_TemplateMatch": {
                "recognition": {"type": "TemplateMatch", "param": {"template": "unused-test.png"}}
            },
            "UnitTest_Override_OCR": {
                "recognition": {"type": "OCR", "param": {"threshold": 0.9, "only_rec": False}},
                "on_error": [],
            },
            "UnitTest_Common_Default": {},
        }
        if not cls.resource.override_pipeline(probes):
            raise AssertionError("The defaults probe nodes failed to parse")

    @classmethod
    def tearDownClass(cls):
        cls.resource = None

    def test_recognition_defaults_use_v2(self):
        for algorithm in ("TemplateMatch", "OCR"):
            with self.subTest(algorithm=algorithm):
                self.assertEqual(set(self.defaults[algorithm]), {"recognition"})
                recognition = self.defaults[algorithm]["recognition"]
                self.assertEqual(recognition["type"], algorithm)
                self.assertIsInstance(recognition["param"], dict)

    def test_common_defaults_and_error_handler_are_inherited(self):
        self.assertNotIn("on_error", self.defaults)
        node = self.resource.get_node_data("UnitTest_Common_Default")
        for field in ("rate_limit", "timeout", "pre_delay", "post_delay"):
            with self.subTest(field=field):
                self.assertEqual(node[field], self.defaults["Default"][field])
        self.assertEqual(
            [entry["name"] for entry in node["on_error"]],
            self.defaults["Default"]["on_error"],
        )

    def test_algorithm_parameters_are_inherited_by_sdk(self):
        for algorithm in ("TemplateMatch", "OCR"):
            node = self.resource.get_node_data("UnitTest_Default_" + algorithm)
            for field, value in self.defaults[algorithm]["recognition"]["param"].items():
                with self.subTest(algorithm=algorithm, field=field):
                    if algorithm == "TemplateMatch" and field == "threshold" and isinstance(value, (int, float)):
                        value = [value]  # MaaFramework resolves scalar thresholds to per-template lists.
                    self.assertEqual(node["recognition"]["param"][field], value)

    def test_explicit_node_settings_override_defaults(self):
        node = self.resource.get_node_data("UnitTest_Override_OCR")
        self.assertEqual(node["recognition"]["param"]["threshold"], 0.9)
        self.assertFalse(node["recognition"]["param"]["only_rec"])
        self.assertEqual(node["on_error"], [])


if __name__ == "__main__":
    unittest.main()
