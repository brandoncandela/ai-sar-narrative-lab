import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT, draft_record, good_narrative
import build

KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}


def repo_files():
    for path in ROOT.rglob("*"):
        if path.is_file() and not (set(path.relative_to(ROOT).parts) & SKIP_DIRS) and not path.name.startswith(".env"):
            yield path


class BuildTests(unittest.TestCase):
    def make_root(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "data").mkdir()
        shutil.copy(ROOT / "data" / "case_packets.json", tmp / "data" / "case_packets.json")
        (tmp / "drafts").mkdir()
        (tmp / "drafts" / "CASE-0033.json").write_text(json.dumps(draft_record(good_narrative())))
        return tmp

    def test_build_writes_the_demo_data_and_is_repeatable(self):
        root = self.make_root()
        lab = build.build(root)
        first = (root / "dist" / "data" / "lab.json").read_text()
        build.build(root)
        self.assertEqual(first, (root / "dist" / "data" / "lab.json").read_text())
        self.assertEqual(lab["meta"]["drafts"], 1)
        self.assertEqual(lab["evaluation"]["pass"], 1)
        self.assertEqual(lab["faults"]["caught"], lab["faults"]["planted"])
        undrafted = [c for c in lab["cases"] if c["draft"] is None]
        self.assertEqual(len(undrafted), 23)
        self.assertIn("evidence packet", lab["system_prompt"])

    def test_build_output_has_no_keys(self):
        root = self.make_root()
        build.build(root)
        self.assertIsNone(KEY_PATTERN.search((root / "dist" / "data" / "lab.json").read_text()))


class SecretSafetyTests(unittest.TestCase):
    def test_no_api_key_anywhere_in_the_repo(self):
        for path in repo_files():
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            self.assertIsNone(KEY_PATTERN.search(text), f"possible API key in {path.relative_to(ROOT)}")

    def test_env_files_are_ignored_by_git(self):
        ignore = (ROOT / ".gitignore").read_text().split()
        self.assertIn(".env", ignore)
        self.assertIn(".env.*", ignore)
        if shutil.which("git") and (ROOT / ".git").exists():
            out = subprocess.run(["git", "check-ignore", ".env", ".env.local"], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(out.stdout.split(), [".env", ".env.local"])

    def test_the_demo_never_calls_a_model(self):
        dist = ROOT / "dist"
        for path in list(dist.glob("*.js")) + list(dist.glob("*.html")):
            text = path.read_text()
            self.assertNotIn("api.anthropic.com", text, path.name)
            self.assertNotIn("x-api-key", text.lower(), path.name)
            for url in re.findall(r"fetch\(\s*[\"'`]([^\"'`]+)", text):
                self.assertFalse(url.startswith(("http:", "https:", "//")), f"{path.name} fetches {url}")

    def test_the_demo_blocks_outside_connections(self):
        html = (ROOT / "dist" / "index.html").read_text()
        csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html)
        self.assertIsNotNone(csp)
        self.assertIn("connect-src 'self'", csp.group(1))


if __name__ == "__main__":
    unittest.main()
