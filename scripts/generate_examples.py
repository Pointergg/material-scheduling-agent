"""Run real scenarios; preserve every actual outcome, including failed assertions."""
from __future__ import annotations
import argparse
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from src.agent import SchedulingAgent
from src.domain import ScheduleRequest
from src.planner import HTTPPlanner, OfflinePlanner
from src.storage import DraftStore

ROOT = Path(__file__).resolve().parents[1]

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def generate(mode, output):
    started = time.perf_counter()
    report = {"mode": mode, "started_at_utc": datetime.now(timezone.utc).isoformat(), "cases": []}
    output.mkdir(parents=True, exist_ok=True)
    q0 = {"request_id": "eval-normal", "material_id": "M01", "date": "2026-11-02", "required_time": "18:00"}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        def scenario(name, request, expected, occupied=None, fail=False, repeats=1, max_steps=12, mutate_after_first=False):
            work = root / name
            data = work / "data"
            shutil.copytree(ROOT / "data", data)
            slots = json.loads((data / "slots.json").read_text(encoding="utf-8"))
            if occupied is not None:
                slots["occupied"][request["date"] + "/main"] = occupied
                write(data / "slots.json", slots)
            db = work / "drafts.sqlite3"
            outputs = []
            before_counts = []
            start = time.perf_counter()
            for i in range(repeats):
                if i and mutate_after_first:
                    slots["occupied"][request["date"] + "/main"] = ["18:00"]
                    write(data / "slots.json", slots)
                before_counts.append(len(DraftStore(db).all()))
                planner = HTTPPlanner() if mode == "live" else OfflinePlanner()
                actual = SchedulingAgent(data, db, planner, max_steps, fail).run(request)
                outputs.append(actual)
            actual = outputs[-1]
            result = actual["result"]
            rows = DraftStore(db).all()
            checks = {key: result.get(key) == value for key, value in expected.items() if key != "draft_count"}
            checks["draft_count"] = len(rows) == expected["draft_count"]
            checks["not_published"] = all(not x["result"]["published"] for x in outputs)
            if name == "duplicate":
                checks["same_draft_id"] = outputs[0]["result"]["draft_id"] == outputs[1]["result"]["draft_id"]
            evidence = {"scenario": name, "request": request, "initial_occupied_override": occupied,
                        "save_failure_injected": fail, "repeat_count": repeats, "mutate_occupancy_after_first": mutate_after_first,
                        "expected": expected, "actual_runs": outputs, "database_row_counts_before": before_counts,
                        "database_rows_after": rows, "checks": checks, "passed": all(checks.values())}
            write(output / (name + ".input.json"), request)
            write(output / (name + ".output.json"), evidence)
            report["cases"].append({"name": name, "passed": evidence["passed"], "checks": checks,
                                    "actual_status": result["run_status"], "draft_status": result["draft_status"],
                                    "steps": sum(x["execution"]["steps"] for x in outputs),
                                    "elapsed_seconds": round(time.perf_counter() - start, 3),
                                    "evidence": name + ".output.json"})
            write(output / "summary.json", report)
            print(f"{mode}: {name}: {'PASS' if evidence['passed'] else 'FAIL'} ({result['run_status']}, {result['draft_status']})", flush=True)

        scenario("normal", q0, {"run_status": "completed", "draft_status": "saved", "scheduled_time": "18:00", "draft_count": 1})
        scenario("missing", {**q0, "material_id": "M02"}, {"run_status": "waiting_information", "review_status": "missing_information", "draft_count": 0})
        scenario("risk", {**q0, "material_id": "M03"}, {"run_status": "blocked", "review_status": "blocked", "draft_count": 0})
        scenario("supplement", {**q0, "material_id": "M05"}, {"run_status": "completed", "review_status": "passed", "draft_count": 1})
        scenario("manual_review", {**q0, "material_id": "M06"}, {"run_status": "waiting_information", "review_status": "needs_review", "draft_count": 0})
        conflict = {**q0, "request_id": "eval-conflict", "date": "2026-11-01"}
        scenario("conflict", conflict, {"run_status": "waiting_confirmation", "suggested_time": "19:00", "draft_status": "not_saved", "draft_count": 0})
        confirmed = {**conflict, "approval": {"approved_time": "19:00", "proposal_id": ScheduleRequest.parse(conflict).proposal_id("19:00")}}
        scenario("confirmed", confirmed, {"run_status": "completed", "scheduled_time": "19:00", "draft_count": 1})
        scenario("duplicate", q0, {"run_status": "completed", "draft_status": "duplicate", "draft_count": 1}, repeats=2)
        scenario("save_failure", q0, {"run_status": "failed", "draft_status": "failed", "draft_count": 0}, fail=True)
        scenario("changed_occupancy", conflict, {"run_status": "waiting_confirmation", "suggested_time": "20:00", "draft_count": 0}, occupied=["18:00", "19:00"])
        scenario("stale_approval", confirmed, {"run_status": "waiting_confirmation", "suggested_time": "20:00", "draft_count": 0}, occupied=["18:00", "19:00"])
        scenario("duplicate_after_occupancy_change", q0, {"run_status": "blocked", "draft_status": "existing_conflict", "draft_count": 1}, repeats=2, mutate_after_first=True)
        scenario("no_slots", q0, {"run_status": "blocked", "draft_count": 0}, occupied=["18:00", "19:00", "20:00"])
        scenario("prompt_injection", {**q0, "material_id": "M07"}, {"run_status": "blocked", "draft_count": 0})
        scenario("step_limit", q0, {"run_status": "incomplete", "draft_count": 0}, max_steps=2)
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    report["passed"] = sum(x["passed"] for x in report["cases"])
    report["total"] = len(report["cases"])
    report["scope_note"] = "合成素材与固定用例的单次验证；不是生产准确率。offline 是测试替身，live 才包含真实模型决策。"
    write(output / "summary.json", report)
    return 0 if report["passed"] == report["total"] else 1

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["offline", "live"], default="offline")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    return generate(args.mode, args.output or ROOT / "examples" / args.mode)

if __name__ == "__main__":
    raise SystemExit(main())
