"""Fast execution tests for Agent routing without loading the application's weights.

The production functions are compiled from their AST unchanged. This avoids the
heavy model startup in main.py while exercising control flow, not string matches.
The opt-in agent_tools_real_smoke.py separately verifies real checkpoints.
"""
from __future__ import annotations

import ast
from contextlib import nullcontext
from pathlib import Path
from threading import Event, RLock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def load_function(filename, function_name, namespace, class_name=None):
    module = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
    nodes = module.body
    if class_name:
        nodes = next(node for node in nodes if isinstance(node, ast.ClassDef) and node.name == class_name).body
    node = next(node for node in nodes if isinstance(node, ast.FunctionDef) and node.name == function_name)
    compiled = ast.Module(body=[node], type_ignores=[])
    exec(compile(compiled, str(ROOT / filename), "exec"), namespace)
    return namespace[function_name]


class RoutingError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class AgentPix2PixDispatchTests(unittest.TestCase):
    def setUp(self):
        # Filesystem HTTP/artifact integrity is covered by the real-weight smoke.
        # Keep the routing test in-memory and independent of Windows temp ACLs.
        self.directory = ROOT / ".analysis_tmp" / "pix2pix-routing-test"
        self.generated = self.directory / "generated.png"
        self.copyfile = Mock()
        self.service = SimpleNamespace(
            ready=True,
            ensure_mode=Mock(side_effect=lambda mode: mode),
            describe=Mock(return_value={"ready": True, "status": "source ready", "checkpoint_name": "best_source_generator_joint.pth"}),
            generate_from_path=Mock(return_value={
                "paths": {"generated": str(self.generated), "preview": str(self.generated), "overlay": str(self.generated)},
                "inference_mode": "source_direct", "mode_label": "Pix2Pix 左半图直推",
                "source_layout": "left_half", "network_input_size": [256, 256],
                "checkpoint_name": "best_source_generator_joint.pth", "mask_fill_ratio": .27,
                "note": "跳过 ROI，直接推理。",
            }),
            set_bbox_detector=Mock(side_effect=AssertionError("Direct generation must not configure ROI")),
        )
        self.forbidden_roi = Mock(side_effect=AssertionError("Pix2Pix direct must not call ROI/flip"))
        self.namespace = {
            "pseudoct_service": self.service,
            "model_template": SimpleNamespace(model_xray=None, model_rgb=None),
            "gait_service": SimpleNamespace(health=lambda: {"available": False, "message": "Not loaded"}),
            "PSEUDOCT_INFERENCE_LOCK": RLock(), "MODEL_INFERENCE_LOCK": RLock(),
            "case_registry": SimpleNamespace(activate_session=lambda *a, **k: nullcontext(),
                                             activate_case=lambda *a, **k: nullcontext(),
                                             delete_case=lambda *a: None),
            "model": SimpleNamespace(current_cv_img=None, auto_flip_if_needed=self.forbidden_roi),
            "detect_rgb_region": self.forbidden_roi,
            "load_image_into_xray_state": Mock(return_value={}),
            "AgentToolError": RoutingError, "shutil": SimpleNamespace(copyfile=self.copyfile),
            "LOGGER": SimpleNamespace(warning=Mock()),
            "AgentToolService": SimpleNamespace(artifact_url=lambda *parts: "/api/" + "/".join(parts)),
        }

    def test_direct_plugin_available_without_roi_weights(self):
        availability = load_function("main.py", "agent_plugin_availability", self.namespace)()
        self.assertTrue(availability["pseudo_dr"]["available"])
        self.assertFalse(availability["roi"]["available"])
        self.service.ensure_mode.assert_called_once_with("source_direct")

    def test_direct_plugin_not_enabled_when_direct_weights_unavailable(self):
        self.service.ready = False
        self.service.describe.return_value = {"ready": False, "status": "missing source weights"}
        availability = load_function("main.py", "agent_plugin_availability", self.namespace)()
        self.assertFalse(availability["pseudo_dr"]["available"])

    def test_direct_load_error_does_not_fallback_to_transfer(self):
        self.service.ensure_mode.side_effect = RuntimeError("incompatible source weights")
        availability = load_function("main.py", "agent_plugin_availability", self.namespace)()
        self.assertFalse(availability["pseudo_dr"]["available"])
        self.service.ensure_mode.assert_called_once_with("source_direct")
        self.service.generate_from_path.assert_not_called()

    def test_pseudo_dr_skips_roi_and_transfer_and_returns_direct_metadata(self):
        execute = load_function("main.py", "execute_agent_plugin", self.namespace)
        result = execute("test-owner", "test-case", "test-run", {"plugin_id": "pseudo_dr", "id": "step-1"},
                         {"filename": "left-half.png"}, self.generated, self.directory, Event())
        self.forbidden_roi.assert_not_called()
        self.service.set_bbox_detector.assert_not_called()
        self.service.generate_from_path.assert_called_once()
        self.assertEqual(self.service.generate_from_path.call_args.kwargs["inference_mode"], "source_direct")
        self.assertEqual(self.service.ensure_mode.call_args.args, ("source_direct",))
        self.assertEqual(result["inference_mode"], "source_direct")
        self.assertEqual(result["model_version"], "best_source_generator_joint.pth")
        self.assertEqual(result["source_layout"], "left_half")
        self.assertEqual(result["network_input_size"], [256, 256])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["generated_image"])
        self.assertNotIn("roi_confidence", result.get("metrics", {}))
        destinations = [call.args[1].name for call in self.copyfile.call_args_list]
        self.assertIn("pseudo-dr.png", destinations)
        self.assertIn("generation-preview.png", destinations)

    def test_generator_public_dispatch_never_uses_transfer_for_direct(self):
        generate = load_function("pseudoct_generator.py", "generate_from_path",
                                 {"Dict": dict, "PSEUDOCT_MODE_SOURCE": "source_direct",
                                  "PSEUDOCT_MODE_TRANSFER": "transfer"}, "PseudoCTGenerator")
        service = SimpleNamespace(ensure_mode=Mock(return_value="source_direct"), ready=True,
                                  _generate_source_from_path=Mock(return_value={"inference_mode": "source_direct"}),
                                  _generate_transfer_from_path=Mock(side_effect=AssertionError("Wrong model")))
        result = generate(service, "left-half.png", "test", inference_mode="source_direct")
        self.assertEqual(result["inference_mode"], "source_direct")
        service._generate_source_from_path.assert_called_once_with("left-half.png", "test", 0, "")
        service._generate_transfer_from_path.assert_not_called()

    def test_input_pair_uses_left_half_only(self):
        from PIL import Image
        split = load_function("pseudoct_generator.py", "maybe_split_pix2pix_pair", {"Image": Image})
        pair = Image.new("RGB", (512, 256), "red")
        pair.paste(Image.new("RGB", (256, 256), "blue"), (256, 0))
        left, target, layout = split(pair)
        self.assertEqual(layout, "pair_auto_split")
        self.assertEqual(left.size, (256, 256))
        self.assertEqual(left.getpixel((255, 255)), (255, 0, 0))
        self.assertEqual(target.getpixel((0, 0)), (0, 0, 255))
        supplied_left, target, layout = split(left)
        self.assertEqual(layout, "left_half")
        self.assertIsNone(target)
        self.assertEqual(supplied_left.tobytes(), left.tobytes())


if __name__ == "__main__":
    unittest.main()
