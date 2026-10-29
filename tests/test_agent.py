import copy
import json
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from src.agent import SchedulingAgent
from src.domain import DomainError, ScheduleRequest
from src.planner import Action, OfflinePlanner
from src.storage import DraftStore
from src.tools import BusinessTools

ROOT = Path(__file__).resolve().parents[1]

class ScriptedPlanner(OfflinePlanner):
    def __init__(self, actions):
        self.actions = iter(actions)
    def choose(self, context):
        return next(self.actions, Action("finish", {}, "scripted test action"))

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        shutil.copytree(ROOT / "data", self.data)
        self.db = self.root / "drafts.sqlite3"
        self.request = {"request_id": "test-01", "material_id": "M01", "date": "2026-11-02", "required_time": "18:00"}
    def tearDown(self):
        self.temp.cleanup()
    def run_agent(self, request=None, **kwargs):
        return SchedulingAgent(self.data, self.db, **kwargs).run(request or self.request)
    def change_slots(self, day, occupied):
        path = self.data / "slots.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["occupied"][day + "/main"] = occupied
        path.write_text(json.dumps(data), encoding="utf-8")
    def approved(self, request, slot):
        return {**request, "approval": {"approved_time": slot, "proposal_id": ScheduleRequest.parse(request).proposal_id(slot)}}
    def test_normal(self):
        out = self.run_agent()
        self.assertEqual(out["result"]["draft_status"], "saved")
        self.assertEqual(out["result"]["scheduled_time"], "18:00")
        self.assertFalse(out["result"]["published"])
        self.assertEqual(len(DraftStore(self.db).all()), 1)
    def test_conflict_requires_exact_confirmation(self):
        q = {**self.request, "date": "2026-11-01"}
        out = self.run_agent(q)
        self.assertEqual(out["result"]["suggested_time"], "19:00")
        self.assertEqual(out["result"]["run_status"], "waiting_confirmation")
        self.assertEqual(out["result"]["draft_status"], "not_saved")
        self.assertEqual(len(DraftStore(self.db).all()), 0)
        accepted = self.run_agent(self.approved(q, "19:00"))
        self.assertEqual(accepted["result"]["scheduled_time"], "19:00")
    def test_duplicate_only_one_row_and_same_id(self):
        first, second = self.run_agent(), self.run_agent()
        self.assertEqual(second["result"]["draft_status"], "duplicate")
        self.assertEqual(first["result"]["draft_id"], second["result"]["draft_id"])
        self.assertEqual(len(DraftStore(self.db).all()), 1)
    def test_saved_approval_is_auditable_when_original_request_repeated(self):
        q = {**self.request, "date": "2026-11-01"}
        self.run_agent(self.approved(q, "19:00"))
        out = self.run_agent(q)
        self.assertEqual(out["result"]["draft_status"], "duplicate")
        self.assertEqual(out["result"]["confirmation_status"], "confirmed")
        self.assertEqual(len(DraftStore(self.db).all()), 1)
    def test_whitespace_title_is_missing(self):
        path = self.data / "materials.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["M01"]["title"] = "   "
        path.write_text(json.dumps(raw), encoding="utf-8")
        out = self.run_agent()
        self.assertEqual(out["result"]["review_status"], "missing_information")
    def test_changed_occupancy_rechecks_existing_draft(self):
        first = self.run_agent()
        self.change_slots("2026-11-02", ["18:00"])
        second = self.run_agent()
        self.assertEqual(second["result"]["run_status"], "blocked")
        self.assertEqual(second["result"]["draft_status"], "existing_conflict")
        self.assertEqual(second["result"]["draft_id"], first["result"]["draft_id"])
        self.assertEqual(len(DraftStore(self.db).all()), 1)
    def test_approval_does_not_transfer_to_new_slot(self):
        q = self.approved({**self.request, "date": "2026-11-01"}, "19:00")
        self.change_slots("2026-11-01", ["18:00", "19:00"])
        out = self.run_agent(q)
        self.assertEqual(out["result"]["suggested_time"], "20:00")
        self.assertEqual(out["result"]["confirmation_status"], "pending")
        self.assertEqual(len(DraftStore(self.db).all()), 0)
    def test_confirmation_bound_to_request(self):
        q = self.approved({**self.request, "date": "2026-11-01"}, "19:00")
        q["request_id"] = "different-request"
        self.assertEqual(self.run_agent(q)["result"]["draft_status"], "not_saved")
    def test_generic_confirmed_boolean_rejected(self):
        with self.assertRaises(DomainError):
            self.run_agent({**self.request, "confirmed": True})
    def test_missing_metadata_lookup_then_wait(self):
        out = self.run_agent({**self.request, "material_id": "M02"})
        self.assertEqual(out["result"]["review_status"], "missing_information")
        self.assertEqual(out["result"]["run_status"], "waiting_information")
        self.assertIn("lookup_metadata", [x["action"] for x in out["trace"]])
        self.assertEqual(len(DraftStore(self.db).all()), 0)
    def test_metadata_lookup_can_recover(self):
        out = self.run_agent({**self.request, "material_id": "M05"})
        self.assertEqual(out["result"]["draft_status"], "saved")
        self.assertEqual([x["action"] for x in out["trace"]].count("check_rules"), 2)
    def test_empty_lookup_preserves_missing_evidence(self):
        q = ScheduleRequest.parse({**self.request, "material_id": "M02"})
        t = BusinessTools(self.data, DraftStore(self.db), q)
        t.read_material(); t.check_rules()
        self.assertFalse(t.lookup_metadata()["review_invalidated"])
        self.assertEqual(t.request_information()["status"], "waiting_information")
    def test_missing_information_cannot_be_mislabeled_terminal_block(self):
        q = ScheduleRequest.parse({**self.request, "material_id": "M02"})
        t = BusinessTools(self.data, DraftStore(self.db), q)
        t.read_material(); t.check_rules()
        with self.assertRaisesRegex(DomainError, "request_information"):
            t.block()
    def test_blocking_risk_cannot_be_overridden_by_time_approval(self):
        q = self.approved({**self.request, "material_id": "M03"}, "19:00")
        out = self.run_agent(q)
        self.assertEqual(out["result"]["review_status"], "blocked")
        self.assertEqual(out["result"]["draft_status"], "not_saved")
    def test_manual_review_required(self):
        out = self.run_agent({**self.request, "material_id": "M06"})
        self.assertEqual(out["result"]["review_status"], "needs_review")
        self.assertEqual(out["result"]["run_status"], "waiting_information")
    def test_storage_failure_rolls_back_insert(self):
        out = self.run_agent(fail_save=True)
        self.assertEqual(out["result"]["draft_status"], "failed")
        self.assertEqual(out["result"]["run_status"], "failed")
        self.assertIsNone(out["result"]["draft_id"])
        self.assertEqual(len(DraftStore(self.db).all()), 0)
        self.assertEqual(self.run_agent()["result"]["draft_status"], "saved")
    def test_step_limit_is_incomplete(self):
        out = self.run_agent(max_steps=2)
        self.assertEqual(out["result"]["run_status"], "incomplete")
        self.assertEqual(out["execution"]["stop_reason"], "step_limit")
        self.assertEqual(len(out["trace"]), 2)
    def test_limit_after_commit_preserves_save_fact(self):
        out = self.run_agent(max_steps=4)
        self.assertEqual(out["result"]["run_status"], "incomplete")
        self.assertEqual(out["result"]["draft_status"], "saved")
        self.assertEqual(len(DraftStore(self.db).all()), 1)
    def test_unknown_material(self):
        out = self.run_agent({**self.request, "material_id": "absent"})
        self.assertEqual(out["result"]["run_status"], "blocked")
        self.assertEqual(out["result"]["errors"][0]["code"], "material_not_found")
    def test_no_available_slots(self):
        self.change_slots("2026-11-02", ["18:00", "19:00", "20:00"])
        out = self.run_agent()
        self.assertEqual(out["result"]["run_status"], "blocked")
        self.assertIsNone(out["result"]["suggested_time"])
    def test_idempotency_key_reuse_rejected(self):
        self.run_agent()
        out = self.run_agent({**self.request, "required_time": "19:00"})
        self.assertEqual(out["result"]["run_status"], "blocked")
        self.assertEqual(out["result"]["errors"][0]["code"], "idempotency_key_reused")
        self.assertEqual(len(DraftStore(self.db).all()), 1)
    def test_new_key_cannot_duplicate_same_material(self):
        self.run_agent()
        out = self.run_agent({**self.request, "request_id": "new-id", "required_time": "19:00"})
        self.assertEqual(out["result"]["errors"][0]["code"], "material_already_scheduled")
        self.assertEqual(len(DraftStore(self.db).all()), 1)
    def test_draft_reserves_slot_for_other_materials(self):
        self.run_agent()
        out = self.run_agent({**self.request, "request_id": "other", "material_id": "M04"})
        self.assertEqual(out["result"]["run_status"], "waiting_confirmation")
        self.assertEqual(out["result"]["suggested_time"], "19:00")
    def test_concurrent_duplicate_saves(self):
        store = DraftStore(self.db)
        req = ScheduleRequest.parse(self.request)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: store.save(req, "18:00", lambda: None), range(8)))
        self.assertEqual(sum(x["status"] == "saved" for x in results), 1)
        self.assertEqual(sum(x["status"] == "duplicate" for x in results), 7)
        self.assertEqual(len(store.all()), 1)
    def test_concurrent_competing_materials_cannot_double_book(self):
        store = DraftStore(self.db)
        def save(i):
            req = ScheduleRequest.parse({**self.request, "request_id": f"race-{i}", "material_id": f"material-{i}"})
            try:
                return store.save(req, "18:00", lambda: None)["status"]
            except DomainError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(save, range(8)))
        self.assertEqual(results.count("saved"), 1)
        self.assertEqual(results.count("slot_conflict"), 7)
    def test_model_cannot_skip_review(self):
        out = self.run_agent(planner=ScriptedPlanner([Action("save_draft", {"slot": "18:00"}, "skip guards")]), max_steps=2)
        self.assertEqual(out["result"]["errors"][0]["code"], "review_required")
        self.assertEqual(len(DraftStore(self.db).all()), 0)
    def test_model_cannot_forge_approval(self):
        q = {**self.request, "date": "2026-11-01"}
        actions = [Action(x, {}, "test") for x in ["read_material", "check_rules", "query_slots"]]
        actions += [Action("save_draft", {"slot": "19:00"}, "pretend user approved")]
        out = self.run_agent(q, planner=ScriptedPlanner(actions), max_steps=4)
        self.assertEqual(out["result"]["errors"][0]["code"], "confirmation_required")
        self.assertEqual(len(DraftStore(self.db).all()), 0)
    def test_no_publish_tool(self):
        out = self.run_agent(planner=ScriptedPlanner([Action("publish", {}, "publish now")]), max_steps=1)
        self.assertEqual(out["result"]["errors"][0]["code"], "unknown_action")
        self.assertFalse(out["result"]["published"])
    def test_metadata_prompt_injection_is_data(self):
        out = self.run_agent({**self.request, "material_id": "M07"})
        self.assertEqual(out["result"]["run_status"], "blocked")
        self.assertFalse(out["result"]["published"])
    def test_save_time_recheck_catches_external_change(self):
        req = ScheduleRequest.parse(self.request)
        t = BusinessTools(self.data, DraftStore(self.db), req)
        t.read_material(); t.check_rules(); t.query_slots()
        self.change_slots(req.date, ["18:00"])
        with self.assertRaisesRegex(DomainError, "时段不可用"):
            t.save_draft("18:00")
        self.assertEqual(len(t.store.all()), 0)
    def test_save_time_recheck_catches_new_risk(self):
        t = BusinessTools(self.data, DraftStore(self.db), ScheduleRequest.parse(self.request))
        t.read_material(); t.check_rules(); t.query_slots()
        path = self.data / "materials.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["M01"]["sensitive_topics"] = ["medical_claim"]
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(DomainError):
            t.save_draft("18:00")
        self.assertEqual(len(t.store.all()), 0)
    def test_invalid_inputs(self):
        for key, value in [("date", "2026-02-30"), ("required_time", "25:00"), ("timezone", "UTC"), ("material_id", "../secret")]:
            with self.subTest(key=key), self.assertRaises(DomainError):
                ScheduleRequest.parse({**self.request, key: value})
    def test_model_failure_is_not_mock_success(self):
        with patch.object(OfflinePlanner, "choose", side_effect=DomainError("model_http_401", "authentication failed")):
            out = self.run_agent()
        self.assertEqual(out["result"]["run_status"], "failed")
        self.assertEqual(out["execution"]["stop_reason"], "model_error")
        self.assertEqual(out["result"]["draft_status"], "not_saved")
    def test_trace_contains_observation_and_actual_next_action(self):
        out = self.run_agent()
        for a, b in zip(out["trace"], out["trace"][1:]):
            self.assertEqual(a["next_action"], b["action"])
            self.assertIn("ok", a["return_summary"])
            self.assertTrue(a["decision_basis"])

if __name__ == "__main__":
    unittest.main()
