"""Local SQLite is the authority for saves, deduplication and reservations."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable

from .domain import DomainError, ScheduleRequest, digest


class DraftStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS drafts (
                draft_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL, material_id TEXT NOT NULL,
                date TEXT NOT NULL, channel TEXT NOT NULL, scheduled_time TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE(material_id, date, channel),
                UNIQUE(date, channel, scheduled_time))""")

    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def get(self, request_id: str) -> dict | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT payload FROM drafts WHERE request_id=?", (request_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self) -> list[dict]:
        with closing(self.connect()) as db:
            rows = db.execute("SELECT payload FROM drafts ORDER BY request_id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def reservations(self, request: ScheduleRequest) -> list[str]:
        with closing(self.connect()) as db:
            rows = db.execute("SELECT scheduled_time FROM drafts WHERE date=? AND channel=? AND request_id<>?",
                              (request.date, request.channel, request.request_id)).fetchall()
        return [row[0] for row in rows]

    def save(self, request: ScheduleRequest, slot: str, revalidate: Callable[[], None], fail=False) -> dict:
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT fingerprint,payload FROM drafts WHERE request_id=?", (request.request_id,)).fetchone()
            if existing and existing[0] != request.fingerprint:
                raise DomainError("idempotency_key_reused", "相同 request_id 的原始请求已改变，禁止覆盖旧草稿")
            if existing:
                draft = json.loads(existing[1])
                if slot != draft["scheduled_time"]:
                    raise DomainError("existing_draft_requires_review", "已存在其他时段草稿，不能自动创建第二份或改期")
            revalidate()
            if existing:
                db.commit()
                return {"status": "duplicate", "saved": True, "draft": draft}
            same_material = db.execute("SELECT draft_id FROM drafts WHERE material_id=? AND date=? AND channel=?",
                                       (request.material_id, request.date, request.channel)).fetchone()
            if same_material:
                raise DomainError("material_already_scheduled", "该素材当日同渠道已有草稿，需要人工处理而非重复排期")
            taken = db.execute("SELECT 1 FROM drafts WHERE date=? AND channel=? AND scheduled_time=?",
                               (request.date, request.channel, slot)).fetchone()
            if taken:
                raise DomainError("slot_conflict", "保存时发现时段已被另一请求占用，请重新查询")
            draft = {**request.core(), "draft_id": "draft-" + digest(request.request_id)[:20],
                     "scheduled_time": slot, "status": "draft", "published": False,
                     "approval": request.approval if slot != request.required_time else None,
                     "fingerprint": request.fingerprint}
            db.execute("INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?)",
                       (draft["draft_id"], request.request_id, request.fingerprint, request.material_id,
                        request.date, request.channel, slot, json.dumps(draft, ensure_ascii=False)))
            if fail:
                raise sqlite3.OperationalError("injected storage failure before commit")
            db.commit()
            return {"status": "saved", "saved": True, "draft": draft}
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
