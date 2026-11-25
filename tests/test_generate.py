import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from helpers import good_narrative, packet
import generate
import prompt


def fake_response(text=None, stop_reason="end_turn", model="claude-opus-5-5", extra_blocks=()):
    content = [SimpleNamespace(type="thinking", thinking="")] + list(extra_blocks)
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(content=content, stop_reason=stop_reason, model=model, stop_details=None,
                           usage=SimpleNamespace(input_tokens=4000, output_tokens=3000))


class FakeClient:
    def __init__(self, response):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self._response = response

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class RequestTests(unittest.TestCase):
    def test_request_uses_the_current_model_structured_output_and_fallbacks(self):
        req = generate.build_request(packet())
        self.assertEqual(req["model"], "claude-opus-5-5")
        self.assertEqual(req["output_config"]["effort"], "high")
        self.assertEqual(req["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(req["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", req["betas"])
        for removed in ("temperature", "top_p", "top_k", "thinking"):
            self.assertNotIn(removed, req)

    def test_prompt_carries_the_evidence_but_not_internal_routing(self):
        text = generate.build_request(packet())["messages"][0]["content"]
        for eid in ("KYC-00576315", "CASE-0033", "ACT-263121", "LEG-01581", "FR-4523"):
            self.assertIn(eid, text)
        self.assertNotIn("priority_tier", text)
        self.assertNotIn("primary_typology", text)

    def test_schema_requires_every_section_and_nothing_else(self):
        s = prompt.OUTPUT_SCHEMA
        self.assertEqual(set(s["required"]), set(prompt.SECTIONS) | {"open_questions"})
        self.assertFalse(s["additionalProperties"])
        self.assertFalse(s["properties"]["who"]["items"]["additionalProperties"])


class ResponseTests(unittest.TestCase):
    def test_a_good_reply_is_parsed_and_costed(self):
        client = FakeClient(fake_response(json.dumps(good_narrative())))
        record = generate.draft_one(client, packet())
        self.assertEqual(client.calls[0]["model"], "claude-opus-5-5")
        self.assertEqual(record["narrative"]["who"][0]["citations"], ["KYC-00576315"])
        self.assertIsNone(record["error"])
        self.assertAlmostEqual(record["estimated_cost_usd"], 4000 / 1e6 * 4 + 3000 / 1e6 * 20)
        self.assertFalse(record["fallback_used"])

    def test_refusal_cutoff_and_bad_json_are_recorded_not_hidden(self):
        r = generate.record_from_response(packet(), fake_response(None, stop_reason="refusal"), "t")
        self.assertIsNone(r["narrative"])
        self.assertIn("refused", r["error"])
        r = generate.record_from_response(packet(), fake_response('{"who": [', stop_reason="max_tokens"), "t")
        self.assertIn("max_tokens", r["error"])
        r = generate.record_from_response(packet(), fake_response("not json"), "t")
        self.assertIn("not valid JSON", r["error"])
        self.assertEqual(r["raw_text"], "not json")

    def test_a_fallback_model_is_flagged(self):
        r = generate.record_from_response(packet(), fake_response(json.dumps(good_narrative()), model="claude-opus-4-8",
                                                                  extra_blocks=[SimpleNamespace(type="fallback")]), "t")
        self.assertTrue(r["fallback_used"])
        self.assertEqual(r["model_served"], "claude-opus-4-8")


class KeyHandlingTests(unittest.TestCase):
    def test_env_file_never_overrides_and_ignores_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("# comment\nexport SAR_TEST_A='one'\nSAR_TEST_B=\"two\"\nSAR_TEST_C=three\nnot a line\n")
            with mock.patch.dict(os.environ, {"SAR_TEST_C": "kept"}, clear=False):
                names = generate.load_env_file(env)
                self.assertEqual(os.environ["SAR_TEST_A"], "one")
                self.assertEqual(os.environ["SAR_TEST_B"], "two")
                self.assertEqual(os.environ["SAR_TEST_C"], "kept")
                self.assertEqual(sorted(names), ["SAR_TEST_A", "SAR_TEST_B"])
                for n in names:
                    os.environ.pop(n)

    def test_dry_run_needs_no_key_and_never_calls_the_api(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(generate, "make_client") as make, \
                mock.patch.object(generate, "DRAFTS", Path(tempfile.mkdtemp())), redirect_stdout(out):
            code = generate.main(["--case", "CASE-0033", "--dry-run"])
        self.assertEqual(code, 0)
        make.assert_not_called()
        self.assertIn("claude-opus-5-5", out.getvalue())

    def test_missing_key_stops_before_any_request(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(generate, "make_client") as make, \
                mock.patch.object(generate, "ENV_FILE", Path(tempfile.mkdtemp()) / ".env"), \
                mock.patch.object(generate, "load_env_file", lambda path=None: []), \
                mock.patch.object(generate, "DRAFTS", Path(tempfile.mkdtemp())), redirect_stderr(err):
            code = generate.main(["--case", "CASE-0033"])
        self.assertEqual(code, 2)
        make.assert_not_called()
        self.assertIn(".env", err.getvalue())

    def test_saved_drafts_hold_no_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-" + "ant-" + "test-not-a-real-key"}):
                record = generate.draft_one(FakeClient(fake_response(json.dumps(good_narrative()))), packet())
                path = generate.save(record, Path(tmp))
            saved = path.read_text()
        self.assertNotIn("sk-ant", saved)
        self.assertNotIn("api_key", saved.lower())


if __name__ == "__main__":
    unittest.main()
