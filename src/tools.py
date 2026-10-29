"""Trusted business tools; model-selected actions cannot bypass these guards."""
from __future__ import annotations

import json
from pathlib import Path

from .domain import DomainError, ScheduleRequest, valid_time
from .storage import DraftStore


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


class BusinessTools:
    def __init__(self, data_dir: Path, store: DraftStore, request: ScheduleRequest, fail_save=False):
        self.data_dir, self.store, self.request = Path(data_dir), store, request
        self.fail_save = fail_save
        self.material = None
        self.review = None
        self.slots = None
        self.supplemented = False
        self.proposal = None
        self.save_result = None
        self.terminal = None
        self.errors = []

    def _material(self):
        raw = read_json(self.data_dir / "materials.json").get(self.request.material_id)
        if raw is None:
            raise DomainError("material_not_found", "未找到素材，请确认素材 ID")
        material = dict(raw)
        if self.supplemented:
            supplement = read_json(self.data_dir / "supplements.json").get(self.request.material_id, {})
            for key, value in supplement.items():
                if key in {"title", "content_type", "license", "sensitive_topics"} and material.get(key) in (None, ""):
                    material[key] = value
        return material

    def _review(self, material):
        rules = read_json(self.data_dir / "rules.json")
        missing = [key for key in rules["required_fields"] if material.get(key) is None or (isinstance(material.get(key), str) and not material[key].strip())]
        risks = []
        if missing:
            risks.append({"code": "missing_metadata", "severity": "missing", "message": "缺少字段：" + ", ".join(missing)})
        if ("title" not in missing and not isinstance(material.get("title"), str)) or material.get("content_type") not in {"video", "image", "text", None, ""}:
            risks.append({"code": "invalid_metadata", "severity": "block", "message": "标题或内容类型格式非法"})
        if material.get("status") != "ready":
            risks.append({"code": "invalid_material_status", "severity": "block", "message": "素材不是 ready 状态"})
        license_status = material.get("license")
        if license_status not in (None, "", "cleared"):
            risks.append({"code": "license_not_cleared", "severity": "block", "message": "版权授权未确认，不允许排期"})
        topics = material.get("sensitive_topics", [])
        if not isinstance(topics, list) or any(not isinstance(x, str) for x in topics):
            risks.append({"code": "invalid_metadata", "severity": "block", "message": "风险标签格式非法"})
            topics = []
        for topic in topics:
            if topic in rules["blocked_topics"]:
                risks.append({"code": topic, "severity": "block", "message": rules["blocked_topics"][topic]})
            elif topic in rules["manual_review_topics"]:
                risks.append({"code": topic, "severity": "review", "message": rules["manual_review_topics"][topic]})
        status = "passed"
        if any(x["severity"] == "block" for x in risks):
            status = "blocked"
        elif missing:
            status = "missing_information"
        elif risks:
            status = "needs_review"
        return {"status": status, "missing_fields": missing, "risks": risks, "rules_version": rules["version"]}

    def _external_slots(self):
        source = read_json(self.data_dir / "slots.json")
        key = f"{self.request.date}/{self.request.channel}"
        available = source["available"].get(key, [])
        occupied = source["occupied"].get(key, [])
        if any(not valid_time(x) for x in available + occupied):
            raise DomainError("invalid_slots", "时段数据格式非法")
        return available, occupied

    def _require_passed(self):
        if not self.review or self.review["status"] != "passed":
            raise DomainError("review_required", "必须先读取素材并通过规则检查")

    def read_material(self):
        self.material = self._material()
        self.review = None
        return {"material_id": self.request.material_id, "metadata": self.material}

    def lookup_metadata(self):
        if self.material is None:
            raise DomainError("read_required", "需要先读取素材")
        self.supplemented = True
        before = dict(self.material)
        self.material = self._material()
        # An empty lookup does not invalidate the already observed missing-field evidence.
        # Changed metadata must be checked again before scheduling.
        if before != self.material:
            self.review = None
        return {"source": "local_metadata_catalog", "found_fields": [k for k, v in self.material.items() if before.get(k) != v],
                "metadata": self.material, "review_invalidated": self.review is None}

    def check_rules(self):
        if self.material is None:
            raise DomainError("read_required", "需要先读取素材")
        self.material = self._material()
        self.review = self._review(self.material)
        return self.review

    def query_slots(self):
        self._require_passed()
        available, occupied = self._external_slots()
        existing = self.store.get(self.request.request_id)
        if existing and existing["fingerprint"] != self.request.fingerprint:
            raise DomainError("idempotency_key_reused", "request_id 已被不同请求使用")
        reserved = self.store.reservations(self.request)
        free = sorted(set(available) - set(occupied) - set(reserved))
        self.slots = {"requested_time": self.request.required_time, "requested_available": self.request.required_time in free,
                      "available_slots": free, "externally_occupied": occupied, "draft_reservations": reserved,
                      "existing_draft": existing,
                      "existing_conflict": bool(existing and existing["scheduled_time"] not in free)}
        if self.slots["existing_conflict"]:
            self.save_result = {"status": "existing_conflict", "saved": False, "draft": existing}
        return self.slots

    def request_confirmation(self, slot: str):
        self._require_passed()
        if not self.slots or slot not in self.slots["available_slots"]:
            raise DomainError("slot_not_verified", "只能建议查询结果中的可用时段")
        self.proposal = {"proposal_id": self.request.proposal_id(slot), "requested_time": self.request.required_time,
                         "suggested_time": slot, "date": self.request.date, "channel": self.request.channel,
                         "confirmed": self.request.allows(slot),
                         "approval_payload": {"proposal_id": self.request.proposal_id(slot), "approved_time": slot}}
        self.terminal = None if self.proposal["confirmed"] else "waiting_confirmation"
        return self.proposal

    def save_draft(self, slot: str):
        self._require_passed()
        if not self.slots:
            raise DomainError("query_required", "保存前必须查询时段")
        if not valid_time(slot):
            raise DomainError("invalid_slot", "时段格式不合法")
        existing = self.store.get(self.request.request_id)
        previously_approved = bool(existing and existing["fingerprint"] == self.request.fingerprint
                                   and existing["scheduled_time"] == slot and existing.get("approval"))
        if not self.request.allows(slot) and not previously_approved:
            raise DomainError("confirmation_required", "硬性时段变更需要用户对这个建议的明确确认")

        def revalidate():
            self.review = self._review(self._material())
            if self.review["status"] != "passed":
                raise DomainError("review_changed", "保存前复检未通过，必须重新处理风险")
            available, occupied = self._external_slots()
            if slot not in available or slot in occupied:
                raise DomainError("slot_conflict", "保存前复检发现时段不可用，请重新查询")

        self.save_result = self.store.save(self.request, slot, revalidate, self.fail_save)
        self.terminal = "completed"
        return self.save_result

    def request_information(self):
        if self.review is None or self.review["status"] not in {"missing_information", "needs_review"}:
            raise DomainError("no_information_gap", "当前没有经过核实的资料缺口或人工复核需求")
        self.terminal = "waiting_information"
        return {"status": self.terminal, "required_items": self.review["risks"]}

    def block(self):
        if self.review and self.review["status"] in {"missing_information", "needs_review"}:
            raise DomainError("information_request_required", "资料缺失或待复核需要调用 request_information，不应当作不可恢复的阻断")
        evidence = (self.review and self.review["status"] != "passed") or (self.slots and (not self.slots["available_slots"] or self.slots["existing_conflict"])) or self.errors
        if not evidence:
            raise DomainError("block_without_evidence", "没有工具证据支持阻断")
        self.terminal = "failed" if self.save_result and self.save_result["status"] == "failed" else "blocked"
        return {"status": self.terminal, "risk_reasons": self.review["risks"] if self.review else [], "errors": self.errors}

    def finish(self):
        if self.terminal is None:
            raise DomainError("premature_finish", "尚无已保存草稿或明确的待处理/阻断结果")
        return {"status": self.terminal, "published": False}

    def context(self):
        return {"request": {**self.request.core(), "approval": self.request.approval}, "material_loaded": self.material is not None,
                "review": self.review, "metadata_lookup_done": self.supplemented, "slots": self.slots,
                "proposal": self.proposal, "save_result": self.save_result, "terminal": self.terminal, "errors": self.errors}
