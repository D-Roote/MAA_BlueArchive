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

    def test_click_default_has_no_long_press_duration(self):
        self.assertNotIn("LongPress", self.defaults)
        self.assertEqual(self.defaults["Click"]["action"], {"type": "Click", "param": {"target": True}})

    def test_all_pipeline_actions_have_no_long_press(self):
        def check_actions(value, location):
            if isinstance(value, dict):
                action = value.get("action")
                with self.subTest(location=location):
                    self.assertNotEqual(action, "LongPress")
                    if isinstance(action, dict):
                        self.assertNotEqual(action.get("type"), "LongPress")
                        if action.get("type") == "Click":
                            self.assertNotIn("duration", action.get("param", {}))
                for key, child in value.items():
                    check_actions(child, location + "/" + key)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    check_actions(child, location + "/" + str(index))

        for path in (RESOURCE_DIR / "pipeline").rglob("*.json"):
            check_actions(json.loads(path.read_text(encoding="utf-8-sig")), str(path.relative_to(RESOURCE_DIR)))

    def test_sdk_resolves_clicks_without_duration(self):
        # Parse each file independently: developer backup files may reuse node names.
        for path in (RESOURCE_DIR / "pipeline").rglob("*.json"):
            pipeline = json.loads(path.read_text(encoding="utf-8-sig"))
            self.assertTrue(self.resource.override_pipeline(pipeline), str(path))
            for name, data in pipeline.items():
                if not isinstance(data, dict):
                    continue
                action_data = data.get("action")
                if not isinstance(action_data, dict) or action_data.get("type") != "Click":
                    continue
                with self.subTest(path=path.name, node=name):
                    action = self.resource.get_node_data(name)["action"]
                    self.assertEqual(action["type"], "Click")
                    self.assertNotIn("duration", action["param"])
                    for key, value in action_data.get("param", {}).items():
                        self.assertEqual(action["param"][key], value)


if __name__ == "__main__":
    unittest.main()
