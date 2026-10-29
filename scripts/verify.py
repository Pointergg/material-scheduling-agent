"""Execute unittest and write the actual result, without requiring pytest."""
import io
import json
import platform
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

def main():
    start = time.perf_counter()
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    output = Path("examples/test-results")
    output.mkdir(parents=True, exist_ok=True)
    (output / "unittest.txt").write_text(stream.getvalue(), encoding="utf-8")
    report = {"command": "python -m scripts.verify", "test_command_equivalent": "python -m unittest discover -s tests -v",
              "executed_at_utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
              "platform": platform.system(), "tests_run": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "skipped": len(result.skipped), "successful": result.wasSuccessful(),
              "elapsed_seconds": round(time.perf_counter() - start, 3)}
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(stream.getvalue())
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    raise SystemExit(main())
