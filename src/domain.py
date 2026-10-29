"""Validated caller input. Model outputs never grant confirmation authority."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any


class DomainError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def valid_time(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is not None


@dataclass(frozen=True)
class ScheduleRequest:
    request_id: str
    material_id: str
    date: str
    required_time: str
    channel: str = "main"
    timezone: str = "Asia/Shanghai"
    approval: dict | None = None

    @classmethod
    def parse(cls, value: dict) -> ScheduleRequest:
        if not isinstance(value, dict):
            raise DomainError("invalid_request", "请求必须为 JSON 对象")
        allowed = set(cls.__dataclass_fields__)
        if set(value) - allowed:
            raise DomainError("invalid_request", "存在未知字段；confirmed 布尔值不能代替具体时段确认")
        for key in ("request_id", "material_id", "date", "required_time"):
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise DomainError("invalid_request", f"{key} 必须为非空字符串")
        for key in ("request_id", "material_id", "channel"):
            if not isinstance(value.get(key, "main"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value.get(key, "main")):
                raise DomainError("invalid_request", f"{key} 格式不合法")
        try:
            parsed = date.fromisoformat(value["date"])
            if parsed.isoformat() != value["date"]:
                raise ValueError()
        except ValueError as exc:
            raise DomainError("invalid_request", "date 必须为 YYYY-MM-DD 有效日期") from exc
        if not valid_time(value["required_time"]):
            raise DomainError("invalid_request", "required_time 必须为 HH:MM")
        if value.get("timezone", "Asia/Shanghai") != "Asia/Shanghai":
            raise DomainError("invalid_request", "演示只支持 Asia/Shanghai 时区")
        approval = value.get("approval")
        if approval is not None:
            if not isinstance(approval, dict) or set(approval) != {"proposal_id", "approved_time"}:
                raise DomainError("invalid_approval", "approval 需要 proposal_id 和 approved_time")
            if not valid_time(approval["approved_time"]) or not isinstance(approval["proposal_id"], str):
                raise DomainError("invalid_approval", "确认格式不合法")
        return cls(**value)

    def core(self) -> dict:
        return {key: getattr(self, key) for key in ("request_id", "material_id", "date", "required_time", "channel", "timezone")}

    @property
    def fingerprint(self) -> str:
        return digest(self.core())

    def proposal_id(self, slot: str) -> str:
        return "proposal-" + digest({"request": self.core(), "slot": slot})[:24]

    def allows(self, slot: str) -> bool:
        if slot == self.required_time:
            return True
        return self.approval == {"proposal_id": self.proposal_id(slot), "approved_time": slot}
