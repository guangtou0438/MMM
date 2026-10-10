"""Trace fresh synthetic create/update cases through the real model API.

All calendar writes go to a temporary SQLite database. The saved trace omits
provider credentials and private reasoning text.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from check_function_calling_live import TrackedProvider

from calendar_agent.app import create_app
from calendar_agent.core.date_tasks import today


def wait_for_run(client: TestClient, run_id: str, timeout: float = 180):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        run = client.get(f"/api/agent/runs/{run_id}").json()
        if run["status"] in {"completed", "waiting_for_user", "failed", "cancelled"}:
            return run
        time.sleep(0.1)
    client.post(f"/api/agent/runs/{run_id}/cancel", json={})
    raise TimeoutError(f"Run {run_id} did not finish")


def clean_history(messages):
    return [
        {
            "role": message["role"],
            "tool_call_id": message.get("tool_call_id"),
            "function": (message.get("tool_calls") or [{}])[0]
            .get("function", {})
            .get("name"),
            "content": message.get("content"),
        }
        for message in messages
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=("glm", "deepseek"), default="deepseek")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    clock_time = datetime.now(timezone.utc)
    local_date = today(clock_time)
    tomorrow = local_date + timedelta(days=1)
    day_after = local_date + timedelta(days=2)
    provider = TrackedProvider()
    report = {
        "model": args.model,
        "synthetic_only": True,
        "clock_utc": clock_time.isoformat(),
        "local_date": local_date.isoformat(),
        "expected_tomorrow": tomorrow.isoformat(),
        "expected_day_after": day_after.isoformat(),
        "cases": [],
        "requests": provider.requests,
    }

    with tempfile.TemporaryDirectory(prefix="mmm-native-walkthrough-") as directory:
        app = create_app(
            Path(directory) / "synthetic.db",
            provider,
            clock=lambda: clock_time,
            scheduler_enabled=False,
        )
        with TestClient(app) as client:
            app.state.memory_service.embedding = None
            for name, content in (
                (
                    "create_tomorrow",
                    "请明天创建一项标题严格为「合成图书馆复习 Z」的任务，只创建这一项。",
                ),
                (
                    "update_to_day_after",
                    "把刚创建的「合成图书馆复习 Z」任务从明天改到后天。先读取任务当前版本，只修改计划日期，不创建新任务。",
                ),
            ):
                payload = {
                    "content": content,
                    "request_id": uuid.uuid4().hex,
                    "session_key": "native-walkthrough",
                    "model": args.model,
                    "kind": "complex",
                }
                response = client.post("/api/agent/runs", json=payload)
                response.raise_for_status()
                run_id = response.json()["id"]
                run = wait_for_run(client, run_id)
                tasks = app.state.agent_store.rows(
                    "SELECT id,title,planned_date,deadline_date,version,status FROM calendar_sessions"
                )
                calls = app.state.agent_store.rows(
                    "SELECT skill,tool,arguments,status,result FROM tool_calls WHERE run_id=? ORDER BY created",
                    (run_id,),
                )
                saved = app.state.agent_store.rows(
                    "SELECT messages FROM working_memory WHERE run_id=?", (run_id,)
                )
                expected_date = tomorrow if name == "create_tomorrow" else day_after
                expected_version = 1 if name == "create_tomorrow" else 2
                passed = (
                    run["status"] == "completed"
                    and len(tasks) == 1
                    and tasks[0]["title"] == "合成图书馆复习 Z"
                    and str(tasks[0]["planned_date"]) == expected_date.isoformat()
                    and tasks[0]["version"] == expected_version
                )
                report["cases"].append(
                    {
                        "name": name,
                        "api_payload": payload,
                        "run": {
                            key: run[key]
                            for key in ("id", "status", "model", "answer", "error", "calls", "actual_tokens")
                        },
                        "tool_calls": calls,
                        "history": clean_history(json.loads(saved[0]["messages"])) if saved else [],
                        "tasks_after": tasks,
                        "passed": passed,
                    }
                )
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
                )
                print(
                    json.dumps(
                        {"case": name, "passed": passed, "status": run["status"], "error": run["error"]},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    if not all(case["passed"] for case in report["cases"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
