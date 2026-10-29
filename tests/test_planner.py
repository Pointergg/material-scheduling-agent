import io
import json
import unittest
import urllib.error
from unittest.mock import patch
from src.domain import DomainError
from src.planner import HTTPPlanner

class PlannerTests(unittest.TestCase):
    def planner(self):
        with patch("src.planner.load_env"):
            return HTTPPlanner("https://example.invalid/v1", "test-model", "test-secret-not-real", 1)
    def response(self, calls=None):
        if calls is None:
            calls = [{"id": "call-1", "type": "function", "function": {"name": "read_material", "arguments": json.dumps({"reason": "读取素材"})}}]
        return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": calls}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    def test_tool_observation_protocol_and_usage(self):
        p = self.planner()
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(self.response()).encode())) as call:
            action = p.choose({"request": {"material_id": "M01"}})
        self.assertEqual(action.name, "read_material")
        self.assertEqual(action.arguments, {})
        self.assertEqual(p.usage["total_tokens"], 15)
        request_body = json.loads(call.call_args.args[0].data)
        self.assertEqual(request_body["tool_choice"], "required")
        self.assertFalse(request_body["parallel_tool_calls"])
        p.observe({"ok": True})
        self.assertEqual(p.messages[-1]["tool_call_id"], "call-1")
        self.assertEqual(p.messages[-1]["role"], "tool")
    def test_content_only_success_claim_is_rejected(self):
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(self.response([])).encode())):
            with self.assertRaises(DomainError) as caught:
                self.planner().choose({})
        self.assertEqual(caught.exception.code, "invalid_model_response")
    def test_multiple_tools_rejected(self):
        calls = self.response()["choices"][0]["message"]["tool_calls"] * 2
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(self.response(calls)).encode())):
            with self.assertRaises(DomainError):
                self.planner().choose({})
    def test_malformed_arguments_rejected(self):
        data = self.response()
        data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "not json"
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(data).encode())):
            with self.assertRaises(DomainError):
                self.planner().choose({})
    def test_http_error_does_not_log_key_or_response(self):
        error = urllib.error.HTTPError("https://example.invalid", 401, "test-secret-not-real", {}, io.BytesIO(b"sensitive body"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(DomainError) as caught:
                self.planner().choose({})
        self.assertEqual(caught.exception.code, "model_http_401")
        self.assertNotIn("test-secret-not-real", str(caught.exception))
        self.assertNotIn("sensitive body", str(caught.exception))
    def test_timeout_is_explicit_failure(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError):
            with self.assertRaises(DomainError) as caught:
                self.planner().choose({})
        self.assertEqual(caught.exception.code, "model_unavailable")
    def test_http_endpoint_rejected(self):
        with self.assertRaises(DomainError):
            HTTPPlanner("http://example.invalid", "test", "test")

if __name__ == "__main__":
    unittest.main()
