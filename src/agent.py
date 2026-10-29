"""Bounded model -> tool -> observation loop; results come from trusted state."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from .domain import DomainError, ScheduleRequest
from .planner import DESCRIPTIONS, SLOT_ACTIONS, OfflinePlanner
from .storage import DraftStore
from .tools import BusinessTools

class SchedulingAgent:
    def __init__(self, data_dir="data", db_path="runtime/drafts.sqlite3", planner=None, max_steps=12, fail_save=False):
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or not 1 <= max_steps <= 50:
            raise DomainError("invalid_budget", "max_steps 必须为 1 至 50 的整数")
        self.data_dir, self.db_path = Path(data_dir), db_path
        self.planner = planner or OfflinePlanner()
        self.max_steps, self.fail_save = max_steps, fail_save

    def run(self, raw_request):
        request = ScheduleRequest.parse(raw_request)
        tools = BusinessTools(self.data_dir, DraftStore(self.db_path), request, self.fail_save)
        trace, finished, model_error = [], False, None
        stop_reason = "step_limit"
        for step in range(1, self.max_steps + 1):
            try:
                action = self.planner.choose(tools.context())
            except DomainError as exc:
                model_error = {"code": exc.code, "message": str(exc)}
                stop_reason = "model_error"
                break
            if trace:
                trace[-1]["next_action"] = action.name
            entry = {"step": step, "action": action.name, "tool_params": {"material_id": request.material_id, **action.arguments},
                     "decision_basis": action.reason, "return_summary": None, "next_action": None}
            try:
                if action.name not in DESCRIPTIONS:
                    raise DomainError("unknown_action", "不支持的工具；没有真实发布工具")
                expected = {"slot"} if action.name in SLOT_ACTIONS else set()
                if set(action.arguments) != expected:
                    raise DomainError("invalid_tool_arguments", "工具参数不合法，不能传入额外授权或素材数据")
                if action.name in SLOT_ACTIONS and not isinstance(action.arguments["slot"], str):
                    raise DomainError("invalid_tool_arguments", "slot 必须为字符串")
                observation = {"ok": True, "result": getattr(tools, action.name)(**action.arguments)}
            except DomainError as exc:
                error = {"code": exc.code, "message": str(exc), "action": action.name}
                tools.errors.append(error)
                if exc.code == "slot_conflict":
                    tools.slots, tools.proposal, tools.terminal = None, None, None
                observation = {"ok": False, "error": error}
            except (OSError, sqlite3.Error, json.JSONDecodeError, KeyError, TypeError):
                error = {"code": "storage_failure" if action.name == "save_draft" else "tool_failure",
                         "message": "工具失败，未确认成功；请检查本地数据和存储", "action": action.name}
                tools.errors.append(error)
                if action.name == "save_draft":
                    tools.save_result = {"saved": False, "status": "failed"}
                tools.terminal = "failed"
                observation = {"ok": False, "error": error}
            entry["return_summary"] = observation
            trace.append(entry)
            self.planner.observe(observation)
            if action.name == "finish" and observation["ok"]:
                finished, stop_reason = True, "finished"
                break
        review = tools.review or {"status": "not_checked", "risks": []}
        saved = tools.save_result or {"status": "not_saved", "saved": False}
        draft, proposal = saved.get("draft"), tools.proposal
        conflicts = []
        if tools.slots and not tools.slots["requested_available"]:
            conflicts.append({"code": "requested_time_unavailable", "time": request.required_time,
                              "available_slots": tools.slots["available_slots"],
                              "message": "原要求时段不可用；任何替代时段都需要具体授权"})
        confirmation = ("confirmed" if proposal["confirmed"] else "pending") if proposal else ("confirmed" if draft and draft.get("approval") else "not_required")
        status = tools.terminal if finished else ("failed" if model_error else "incomplete")
        pending = []
        if proposal and not proposal["confirmed"]:
            pending.append({"type": "time_change", **proposal})
        if review["status"] in {"missing_information", "needs_review"}:
            pending.append({"type": "metadata_or_review", "items": review["risks"]})
        if saved["status"] == "existing_conflict":
            pending.append({"type": "existing_draft_conflict", "message": "旧草稿现已冲突，需要人工处理；未自动改期"})
        messages = {
            "completed": "草稿已保存或幂等复用；没有发布任何内容。",
            "waiting_confirmation": "仅提出替代建议，等待用户明确确认；本轮未保存。",
            "waiting_information": "需要补充资料或人工复核；未保存。",
            "blocked": "处理被阻断，请检查风险、冲突或工具错误；未发布。",
            "failed": "处理失败，请查看错误；不能把本轮视为成功排期。",
            "incomplete": "未完成：达到最大执行步数。请同时查看草稿状态，确认是否已有落盘副作用。",
        }
        return {"schema_version": "1.0", "mode": self.planner.mode, "model": self.planner.model_name,
                "request": {**request.core(), "approval": request.approval},
                "result": {"material_id": request.material_id, "run_status": status, "review_status": review["status"],
                    "risk_reasons": review["risks"], "requested_time": request.required_time,
                    "suggested_time": proposal["suggested_time"] if proposal else (draft["scheduled_time"] if draft else None),
                    "scheduled_time": draft["scheduled_time"] if draft else None,
                    "confirmation_status": confirmation, "scheduling_conflicts": conflicts,
                    "pending_items": pending, "draft_status": saved["status"], "draft_id": draft["draft_id"] if draft else None,
                    "published": False, "message": messages[status], "errors": tools.errors + ([model_error] if model_error else [])},
                "execution": {"steps": len(trace), "max_steps": self.max_steps, "stop_reason": stop_reason,
                              "usage": getattr(self.planner, "usage", {})}, "trace": trace}

if __name__ == "__main__":
    from .cli import main
    raise SystemExit(main())
