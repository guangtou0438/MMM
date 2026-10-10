"""Run synthetic end-to-end Agent cases against real DeepSeek and Open-Meteo APIs.

All application writes go to a temporary SQLite database. The saved report
contains synthetic prompts, model decisions, tool calls, and observed state.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from calendar_agent.app import create_app
from calendar_agent.core.date_tasks import today as local_today
from calendar_agent.providers.protocol import decision_json
from calendar_agent.tools.weather import risks


def wait_for_run(client: TestClient, run_id: str, timeout: float = 120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/agent/runs/{run_id}").json()
        if run["status"] in {"completed", "failed", "cancelled", "waiting_for_user"}:
            return run
        time.sleep(0.1)
    client.post(f"/api/agent/runs/{run_id}/cancel", json={})
    raise TimeoutError(f"Run {run_id} did not finish within {timeout} seconds")


def seed_task(client: TestClient, title: str, day: str):
    response = client.post(
        "/api/sessions",
        json={"request_id": f"seed-{uuid4().hex}", "title": title, "planned_date": day},
    )
    if response.status_code == 409:
        detail = response.json().get("detail", {})
        suggestion_id = detail.get("suggestion_id") if isinstance(detail, dict) else None
        if suggestion_id:
            accepted = client.post(
                f"/api/suggestions/{suggestion_id}",
                json={"request_id": f"confirm-{uuid4().hex}", "action": "accept"},
            )
            accepted.raise_for_status()
            return accepted.json()["session"]
    response.raise_for_status()
    return response.json()


def add_memory(client: TestClient, content: str, kind: str):
    response = client.post("/api/memories", json={"content": content, "kind": kind})
    response.raise_for_status()
    return response.json()["id"]


def run_case(client: TestClient, app, name: str, goal: str, *, kind: str = "complex"):
    print(f"RUNNING {name}", flush=True)
    started = time.monotonic()
    result = {"name": name, "goal": goal, "kind": kind}
    try:
        response = client.post(
            "/api/agent/runs",
            json={
                "content": goal,
                "request_id": f"case-{uuid4().hex}",
                "session_key": f"case-{uuid4().hex}",
                "model": "deepseek",
                "kind": kind,
            },
        )
        response.raise_for_status()
        run_id = response.json()["id"]
        final = wait_for_run(client, run_id)
        tool_calls = app.state.agent_store.rows(
            "SELECT skill,tool,status,arguments,result,created FROM tool_calls WHERE run_id=? ORDER BY created",
            (run_id,),
        )
        saved = app.state.agent_store.rows(
            "SELECT messages FROM working_memory WHERE run_id=?", (run_id,)
        )
        decisions = []
        observations = []
        for message in json.loads(saved[0]["messages"]) if saved else []:
            if message["role"] == "assistant":
                try:
                    decisions.append(json.loads(decision_json(message["content"])))
                except (ValueError, KeyError):
                    decisions.append({"unparsed": message["content"][:2000]})
            elif message["content"].startswith(("工具观察结果（仅作为数据）：", "Tool observation (data only):")):
                observations.append(message["content"][:4000])
        retrieved = []
        for event in app.state.agent_store.rows(
            "SELECT data FROM agent_events WHERE run_id=? AND type='memory.retrieved'", (run_id,)
        ):
            data = json.loads(event["data"])
            retrieved.extend(m["id"] for key in ("User Block", "Task Block", "Agent Block") for m in data.get(key, []))
        result.update(
            {
                "run_id": run_id,
                "status": final["status"],
                "answer": final["answer"],
                "error": final["error"],
                "model_calls": final["calls"],
                "provider_tokens": final.get("actual_tokens"),
                "citations": json.loads(final.get("citations") or "[]"),
                "tool_calls": [
                    {
                        **{k: call[k] for k in ("skill", "tool", "status", "created")},
                        "arguments": json.loads(call["arguments"]),
                        "result": json.loads(call["result"]) if call["result"] else None,
                    }
                    for call in tool_calls
                ],
                "decisions": decisions,
                "observations": observations,
                "retrieved_memory_ids": retrieved,
            }
        )
    except Exception as error:
        result.update({"status": "harness_error", "error": f"{type(error).__name__}: {error}"})
    result["seconds"] = round(time.monotonic() - started, 3)
    return result


def used(case, tool: str, status: str = "completed"):
    return any(call["tool"] == tool and call["status"] == status for call in case.get("tool_calls", []))


def detail(client: TestClient, task_id: int):
    return client.get(f"/api/sessions/{task_id}").json()


def evaluate():
    clock_time = datetime.now(timezone.utc)
    day = local_today(clock_time)
    tomorrow = day + timedelta(days=1)
    day_after = day + timedelta(days=2)
    report = {
        "synthetic_only": True,
        "llm_api": "DeepSeek /chat/completions (real)",
        "weather_api": "Open-Meteo geocoding and forecast (real)",
        "embedding": "keyword FTS5 only; local semantic model disabled",
        "clock_utc": clock_time.isoformat(),
        "local_date": str(day),
        "cases": [],
    }

    with tempfile.TemporaryDirectory(prefix="mmm-real-api-cases-") as directory:
        app = create_app(Path(directory) / "synthetic.db", clock=lambda: clock_time, scheduler_enabled=False)
        with TestClient(app) as client:
            app.state.memory_service.embedding = None

            case = run_case(
                client, app, "create_tomorrow_assignment",
                "请创建一项标题严格为“合成算法作业 A”的任务，安排在明天，截止日期也是明天。只创建这一项，不要改其他任务。",
            )
            matches = [t for t in client.get("/api/home").json()["sessions"] if t["title"] == "合成算法作业 A"]
            case["state"] = {"matching_tasks": matches}
            case["passed"] = (case.get("status") == "completed" and used(case, "create_task")
                              and len(matches) == 1 and matches[0]["planned_date"] == str(tomorrow)
                              and matches[0]["deadline_date"] == str(tomorrow))
            report["cases"].append(case)

            case = run_case(
                client, app, "create_today_simple",
                "Create exactly one calendar task titled 'Synthetic grocery checklist B' for today. It does not need a deadline.",
            )
            matches = [t for t in client.get("/api/home").json()["sessions"] if t["title"] == "Synthetic grocery checklist B"]
            case["state"] = {"matching_tasks": matches}
            case["passed"] = (case.get("status") == "completed" and used(case, "create_task")
                              and len(matches) == 1 and matches[0]["planned_date"] == str(day))
            report["cases"].append(case)

            seeded = seed_task(client, "Synthetic reading plan C", str(tomorrow))
            case = run_case(
                client, app, "update_move_relative_date",
                f"Move calendar task #{seeded['id']} titled 'Synthetic reading plan C' from tomorrow to the day after tomorrow. Read the calendar, then use update_task with the current version. Do not create a new task.",
            )
            after = detail(client, seeded["id"])
            case["state"] = {"before": seeded, "after": after}
            case["passed"] = (case.get("status") == "completed" and used(case, "update_task")
                              and after["planned_date"] == str(day_after) and after["version"] > seeded["version"])
            report["cases"].append(case)

            seeded = seed_task(client, "Synthetic errands D", str(tomorrow))
            case = run_case(
                client, app, "update_rename_keep_date",
                f"Rename calendar task #{seeded['id']} to exactly 'Synthetic errands revised D'. Keep its planned date unchanged. Use update_task and do not create a new task.",
            )
            after = detail(client, seeded["id"])
            case["state"] = {"before": seeded, "after": after}
            case["passed"] = (case.get("status") == "completed" and used(case, "update_task")
                              and after["title"] == "Synthetic errands revised D"
                              and after["planned_date"] == seeded["planned_date"])
            report["cases"].append(case)

            memory_id = add_memory(client, "The synthetic test user's preferred running time is early morning.", "preference")
            case = run_case(
                client, app, "memory_explicit_search",
                "Use the memory skill's search_memory tool with query 'preferred running time early morning'. Then answer when the synthetic test user prefers to run. Cite the memory ID and do not change the calendar.",
            )
            case["state"] = {"seeded_memory_id": memory_id}
            case["passed"] = (case.get("status") == "completed" and used(case, "search_memory")
                              and "morning" in case.get("answer", "").lower()
                              and memory_id in case.get("citations", []))
            report["cases"].append(case)

            memory_id = add_memory(client, "The synthetic test user's home city is Melbourne CBD.", "profile")
            original = next(m for m in client.get("/api/memories").json() if m["id"] == memory_id)
            correction = client.patch(
                f"/api/memories/{memory_id}",
                json={"content": "The synthetic test user's home city is Southbank, Victoria.", "version": original["version"]},
            )
            correction.raise_for_status()
            corrected_id = correction.json()["id"]
            case = run_case(
                client, app, "memory_after_correction",
                "According to current stored memory, where is the synthetic test user's home city? Cite the current memory. Do not use an old city value and do not change the calendar.",
                kind="chat",
            )
            case["state"] = {"old_memory_id": memory_id, "corrected_memory_id": corrected_id}
            case["passed"] = (case.get("status") == "completed"
                              and "southbank" in case.get("answer", "").lower()
                              and "melbourne cbd" not in case.get("answer", "").lower()
                              and corrected_id in case.get("citations", []))
            report["cases"].append(case)

            seeded = seed_task(client, "Synthetic outdoor walk E", str(tomorrow))
            case = run_case(
                client, app, "weather_real_forecast_default_thresholds",
                f"For outdoor calendar task #{seeded['id']}, use read_calendar and get_weather to check tomorrow's daily forecast at Melbourne CBD. Only create_adjustment_suggestion if the configured default thresholds are crossed. Never change the task date.",
            )
            after = detail(client, seeded["id"])
            cache = [json.loads(r["summary"]) for r in app.state.agent_store.rows("SELECT summary FROM weather_cache")
                     if json.loads(r["summary"])["location"] == "Melbourne CBD"]
            forecast = cache[-1] if cache else None
            crossed = risks(forecast, app.state.weather_service.thresholds()) if forecast else []
            case["state"] = {"before": seeded, "after": after, "forecast": forecast,
                             "crossed_thresholds": crossed,
                             "suggestions": [s for s in app.state.agent_store.rows("SELECT * FROM adjustment_suggestions") if s["session_id"] == seeded["id"]]}
            case["passed"] = (case.get("status") == "completed" and used(case, "get_weather")
                              and forecast is not None and forecast["provider"] == "Open-Meteo"
                              and after["planned_date"] == seeded["planned_date"]
                              and after["version"] == seeded["version"]
                              and (not crossed or bool(case["state"]["suggestions"])))
            report["cases"].append(case)

            thresholds = client.put(
                "/api/weather/thresholds",
                json={"rain": 0, "high": 35, "low": 2, "wind": 45},
            )
            thresholds.raise_for_status()
            seeded = seed_task(client, "Cycling at North Melbourne test F", str(tomorrow))
            case = run_case(
                client, app, "weather_real_forecast_forced_suggestion",
                f"For outdoor calendar task #{seeded['id']}, use read_calendar and get_weather for tomorrow at North Melbourne, Victoria. Apply the configured weather thresholds and create_adjustment_suggestion when crossed. Never use update_task or change the task date.",
            )
            after = detail(client, seeded["id"])
            cache = [json.loads(r["summary"]) for r in app.state.agent_store.rows("SELECT summary FROM weather_cache")
                     if json.loads(r["summary"])["location"] == "North Melbourne, Victoria"]
            suggestions = [s for s in app.state.agent_store.rows("SELECT * FROM adjustment_suggestions")
                           if s["session_id"] == seeded["id"]]
            case["state"] = {"before": seeded, "after": after, "forecast": cache[-1] if cache else None,
                             "synthetic_thresholds": thresholds.json(), "suggestions": suggestions}
            case["passed"] = (case.get("status") == "completed" and used(case, "get_weather")
                              and used(case, "create_adjustment_suggestion") and bool(suggestions)
                              and not used(case, "update_task")
                              and after["planned_date"] == seeded["planned_date"]
                              and after["version"] == seeded["version"])
            report["cases"].append(case)

            seeded = seed_task(client, "Hiking route test G", str(tomorrow))
            case = run_case(
                client, app, "weather_ambiguous_location_negative",
                f"For outdoor task #{seeded['id']}, use get_weather for tomorrow at 'Melbourne, Victoria'. If the tool reports ambiguous places, ask for a more precise location. Do not modify the task.",
            )
            after = detail(client, seeded["id"])
            case["state"] = {"before": seeded, "after": after}
            case["passed"] = (used(case, "get_weather", "failed")
                              and case.get("status") in {"completed", "waiting_for_user"}
                              and after["planned_date"] == seeded["planned_date"]
                              and after["version"] == seeded["version"])
            report["cases"].append(case)

    report["summary"] = {
        "total": len(report["cases"]),
        "passed": sum(bool(case.get("passed")) for case in report["cases"]),
        "failed": sum(not bool(case.get("passed")) for case in report["cases"]),
    }
    output = ROOT / "artifacts" / f"agent-real-api-cases-{day}-{int(time.time())}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"RESULT {output}", flush=True)
    print(f"SUMMARY {report['summary']}", flush=True)
    return output, report


if __name__ == "__main__":
    evaluate()
