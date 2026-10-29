import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class CLITests(unittest.TestCase):
    def test_cli_saves_output_and_json_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.json"
            cmd = [sys.executable, "-X", "utf8", "-m", "src", "--input", "examples/input_normal.json", "--db", str(Path(tmp) / "drafts.sqlite3"), "--output", str(output)]
            run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout), json.loads(output.read_text(encoding="utf-8")))
    def test_cli_save_failure_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = subprocess.run([sys.executable, "-X", "utf8", "-m", "src", "--input", "examples/input_normal.json", "--db", str(Path(tmp)/"db.sqlite3"), "--save-failure"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(json.loads(run.stdout)["result"]["draft_status"], "failed")
    def test_batch_no_double_booking(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            first = {"request_id": "batch-1", "material_id": "M01", "date": "2026-11-02", "required_time": "18:00"}
            requests = [first, {**first, "request_id": "batch-2", "material_id": "M04"}]
            (p/"input.json").write_text(json.dumps(requests), encoding="utf-8")
            run = subprocess.run([sys.executable, "-X", "utf8", "-m", "src", "--input", str(p/"input.json"), "--db", str(p/"db.sqlite3")], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(run.returncode, 0)
            result = json.loads(run.stdout)
            self.assertEqual(result[0]["result"]["draft_status"], "saved")
            self.assertEqual(result[1]["result"]["run_status"], "waiting_confirmation")

if __name__ == "__main__":
    unittest.main()
