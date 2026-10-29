"""CLI: JSON stdout, explicit failure exit codes, secrets never logged."""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from .agent import SchedulingAgent
from .domain import DomainError
from .planner import HTTPPlanner, OfflinePlanner

def main(argv=None):
    parser = argparse.ArgumentParser(description="素材审核与发布排期 Agent：只保存草稿")
    parser.add_argument("--input", required=True, help="请求 JSON；支持单个对象或数组")
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--db", default="runtime/drafts.sqlite3")
    parser.add_argument("--output", help="同时保存结果及完整 Trace 的 JSON 文件")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--save-failure", action="store_true", help="验收用：INSERT 后、COMMIT 前注入失败并回滚")
    parser.add_argument("--approve-time", help="用户确认的具体替代时段 HH:MM")
    parser.add_argument("--proposal-id", help="上次运行 pending_items 中对应 proposal_id")
    args = parser.parse_args(argv)
    try:
        raw = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        batch = isinstance(raw, list)
        requests = raw if batch else [raw]
        if not requests:
            raise DomainError("invalid_request", "请求列表不能为空")
        if bool(args.approve_time) != bool(args.proposal_id) or (batch and args.approve_time):
            raise DomainError("invalid_approval", "两个确认参数必须同时提供，且只用于单条请求")
        if args.approve_time:
            raw["approval"] = {"approved_time": args.approve_time, "proposal_id": args.proposal_id}
        results = []
        for request in requests:
            planner = HTTPPlanner() if args.mode == "live" else OfflinePlanner()
            agent = SchedulingAgent(args.data_dir, args.db, planner, args.max_steps, args.save_failure)
            results.append(agent.run(request))
        output = results if batch else results[0]
        states = {x["result"]["run_status"] for x in results}
        exit_code = 2 if "failed" in states else (3 if "incomplete" in states else 0)
    except (DomainError, OSError, ValueError, TypeError, sqlite3.Error) as exc:
        output = {"error": {"code": getattr(exc, "code", "input_or_storage_error"),
                            "message": str(exc) if isinstance(exc, DomainError) else "输入、文件或存储不可用，请检查配置"}, "published": False}
        exit_code = 2
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return exit_code
