"""
FastAPI + WebSocket server for the benchmark orchestrator.

Serves the frontend and provides REST + WebSocket APIs for controlling
the pipeline, managing comparisons, and maintaining a leaderboard.
"""

import asyncio
import json
import logging
import os
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import uvicorn

from runner.orchestrator import Orchestrator
from runner.types import ConcurrencyConfig, SamplingConfig

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
COMPARISONS_DIR = os.path.join(DATA_DIR, "comparisons")
LEADERBOARD_PATH = os.path.join(DATA_DIR, "leaderboard.json")

app = FastAPI(title="Context Bench API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

orchestrator = Orchestrator()
_clients: Set[WebSocket] = set()
_running_tasks: Dict[str, asyncio.Task] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _atomic_write_json(path: str, data: Any) -> None:
    _ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


async def ws_broadcast(data: Dict[str, Any]) -> None:
    dead = set()
    for ws in _clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.add(ws)
    for ws in dead:
        _clients.discard(ws)


def _serialize_question(qcp, checkpoint_mgr, run_id: str) -> Dict[str, Any]:
    """Serialize a QuestionCheckpoint to a JSON-safe dict with search results."""
    phases = {}
    if qcp.phases:
        for pname, pobj in qcp.phases.items():
            if isinstance(pobj, dict):
                phases[pname] = pobj
            else:
                phases[pname] = asdict(pobj)
    return {
        "questionId": qcp.question_id,
        "containerTag": qcp.container_tag,
        "question": qcp.question,
        "groundTruth": qcp.ground_truth,
        "questionType": qcp.question_type,
        "questionDate": qcp.question_date,
        "phases": phases,
    }


# ══════════════════════════════════════════════════════════════════
# RUNS
# ══════════════════════════════════════════════════════════════════


@app.post("/api/ingest")
async def start_ingest(body: Dict[str, Any]):
    provider = body.get("provider", "synap")
    benchmark = body.get("benchmark", "longmemeval")
    isolation_mode = body.get("isolation_mode", "global")
    container_tag_prefix = body.get("container_tag_prefix")
    max_groups = body.get("max_groups")

    task_key = f"ingest-{provider}-{benchmark}"
    if task_key in _running_tasks and not _running_tasks[task_key].done():
        return JSONResponse({"error": "Ingest already running"}, status_code=409)

    async def _run():
        try:
            await orchestrator.global_ingest(
                provider, benchmark,
                isolation_mode=isolation_mode,
                container_tag_prefix=container_tag_prefix,
                max_groups=max_groups,
            )
        except Exception as e:
            logger.error("Ingest failed: %s", e)
            await ws_broadcast({"type": "ingest_error", "provider": provider, "benchmark": benchmark, "error": str(e)})

    _running_tasks[task_key] = asyncio.create_task(_run())
    return {"started": True, "provider": provider, "benchmark": benchmark, "isolationMode": isolation_mode}


@app.post("/api/ingest/stop")
async def stop_ingest(body: Dict[str, Any]):
    provider = body.get("provider", "")
    benchmark = body.get("benchmark", "")
    task_key = f"ingest-{provider}-{benchmark}"
    task = _running_tasks.get(task_key)
    if task and not task.done():
        task.cancel()
        return {"stopped": True, "provider": provider, "benchmark": benchmark}
    return {"stopped": False, "note": "No running ingest task found"}


@app.post("/api/run")
async def start_run(body: Dict[str, Any]):
    provider = body.get("provider", "synap")
    benchmark = body.get("benchmark", "longmemeval")
    judge = body.get("judge_model", "gpt-4o")
    model = body.get("answering_model", "gpt-4o")
    run_id = body.get("run_id")
    force = body.get("force", False)
    phases = body.get("phases")
    limit = body.get("limit")
    system_prompt = body.get("system_prompt", "")
    isolation_mode = body.get("isolation_mode", "global")
    retrieval_mode = body.get("retrieval_mode")
    container_tag_prefix = body.get("container_tag_prefix")
    pipelined = bool(body.get("pipelined", False))

    # Build provider-specific config overrides
    provider_config = {}
    if retrieval_mode:
        provider_config["retrieval_mode"] = retrieval_mode
    retrieval_max_results = body.get("retrieval_max_results")
    if retrieval_max_results:
        provider_config["retrieval_max_results"] = retrieval_max_results

    max_per_group = body.get("max_per_group")
    per_record_per_category = body.get("per_record_per_category")
    groups = body.get("groups")  # list of group_ids to restrict to (e.g. ["locomo_1"])
    if limit or max_per_group or per_record_per_category or groups:
        sampling = SamplingConfig(
            mode="limit",
            limit=limit,
            max_per_group=max_per_group,
            per_record_per_category=per_record_per_category,
            groups=groups,
        )
    else:
        sampling = None
    concurrency_val = body.get("concurrency")
    search_conc = body.get("search_concurrency")
    answer_conc = body.get("answer_concurrency")
    evaluate_conc = body.get("evaluate_concurrency")
    if concurrency_val or search_conc or answer_conc or evaluate_conc:
        concurrency = ConcurrencyConfig(
            default=concurrency_val or 1,
            search=search_conc,
            answer=answer_conc,
            evaluate=evaluate_conc,
        )
    else:
        concurrency = None

    # Clean up finished tasks before checking
    for k in list(_running_tasks):
        if _running_tasks[k].done():
            del _running_tasks[k]

    task_key = f"run-{provider}-{benchmark}-{run_id or datetime.now().strftime('%H%M%S')}"
    if task_key in _running_tasks and not _running_tasks[task_key].done():
        return JSONResponse({"error": "Run already in progress"}, status_code=409)

    async def _run():
        try:
            await orchestrator.run(
                provider_name=provider, benchmark_name=benchmark,
                judge_model=judge, answering_model=model, run_id=run_id,
                isolation_mode=isolation_mode, sampling=sampling,
                concurrency=concurrency, phases=phases,
                system_prompt=system_prompt,
                provider_config=provider_config or None,
                container_tag_prefix=container_tag_prefix,
                force=force,
                pipelined=pipelined,
            )
        except Exception as e:
            logger.error("Run failed: %s", e)
            await ws_broadcast({"type": "run_error", "provider": provider, "benchmark": benchmark, "error": str(e)})

    _running_tasks[task_key] = asyncio.create_task(_run())
    return {"started": True, "provider": provider, "benchmark": benchmark, "isolationMode": isolation_mode}


@app.post("/api/runs/{run_id}/reset-phase")
async def reset_and_rerun_phase(run_id: str, body: Dict[str, Any]):
    """Reset a phase for all questions and re-run from that phase onward."""
    from_phase = body.get("from_phase", "evaluate")

    checkpoint = orchestrator.checkpoint_mgr.load(run_id)
    if not checkpoint:
        return JSONResponse({"error": "Run not found"}, status_code=404)

    # Reset phases from the given phase onward
    orchestrator.checkpoint_mgr.reset_from_phase(checkpoint, from_phase)
    orchestrator.checkpoint_mgr._save_sync(checkpoint)

    # Determine which phases to run
    from runner.types import get_phases_from_phase
    phases_to_run = get_phases_from_phase(from_phase)

    # Start the run with only those phases
    provider = checkpoint.provider
    benchmark = checkpoint.benchmark

    task_key = f"run-{provider}-{benchmark}-{run_id}"
    if task_key in _running_tasks and not _running_tasks[task_key].done():
        return JSONResponse({"error": "Run already in progress"}, status_code=409)

    async def _run():
        try:
            await orchestrator.run(
                provider_name=provider, benchmark_name=benchmark,
                judge_model=checkpoint.judge, answering_model=checkpoint.answering_model,
                run_id=run_id, isolation_mode=checkpoint.isolation_mode,
                phases=phases_to_run,
            )
        except Exception as e:
            logger.error("Re-run failed: %s", e)
            await ws_broadcast({"type": "run_error", "provider": provider, "benchmark": benchmark, "error": str(e)})

    _running_tasks[task_key] = asyncio.create_task(_run())
    return {"started": True, "runId": run_id, "phases": phases_to_run}


@app.get("/api/runs")
async def list_runs():
    return orchestrator.list_runs()


@app.get("/api/runs/{run_id}")
async def get_run_detail(run_id: str):
    checkpoint = orchestrator.checkpoint_mgr.load(run_id)
    if not checkpoint:
        return JSONResponse({"error": "Run not found"}, status_code=404)

    summary = orchestrator.checkpoint_mgr.get_summary(checkpoint)
    questions = {}
    for qid, qcp in checkpoint.questions.items():
        questions[qid] = _serialize_question(qcp, orchestrator.checkpoint_mgr, run_id)

    # Compute accuracy from evaluated questions
    evaluated = [q for q in questions.values() if q["phases"].get("evaluate", {}).get("status") == "completed"]
    correct = sum(1 for q in evaluated if q["phases"]["evaluate"].get("label") == "correct")
    accuracy = round(correct / len(evaluated) * 100, 1) if evaluated else None

    return {
        "runId": checkpoint.run_id,
        "provider": checkpoint.provider,
        "benchmark": checkpoint.benchmark,
        "judge": checkpoint.judge,
        "answeringModel": checkpoint.answering_model,
        "isolationMode": checkpoint.isolation_mode,
        "status": checkpoint.status,
        "createdAt": checkpoint.created_at,
        "updatedAt": checkpoint.updated_at,
        "summary": summary,
        "accuracy": accuracy,
        "questions": questions,
    }


@app.get("/api/runs/{run_id}/questions/{question_id}")
async def get_question_detail(run_id: str, question_id: str):
    checkpoint = orchestrator.checkpoint_mgr.load(run_id)
    if not checkpoint:
        return JSONResponse({"error": "Run not found"}, status_code=404)

    qcp = checkpoint.questions.get(question_id)
    if not qcp:
        return JSONResponse({"error": "Question not found"}, status_code=404)

    result = _serialize_question(qcp, orchestrator.checkpoint_mgr, run_id)
    # Load search results from disk
    search_data = orchestrator.checkpoint_mgr.load_search_results(run_id, question_id)
    result["searchResults"] = search_data.get("results", []) if search_data else []
    return result


@app.delete("/api/runs/{run_id}")
async def delete_run(run_id: str):
    run_dir = os.path.join(DATA_DIR, "runs", run_id)
    if not os.path.exists(run_dir):
        return JSONResponse({"error": "Run not found"}, status_code=404)
    shutil.rmtree(run_dir)
    return {"deleted": True, "runId": run_id}


@app.post("/api/runs/{run_id}/stop")
async def stop_run(run_id: str):
    cancelled = False
    for key, task in _running_tasks.items():
        if run_id in key and not task.done():
            task.cancel()
            cancelled = True
            break

    # Always update checkpoint status if it's stuck at "running"
    checkpoint = orchestrator.checkpoint_mgr.load(run_id)
    if checkpoint and checkpoint.status in ("running", "initializing", "pending"):
        checkpoint.status = "failed"
        orchestrator.checkpoint_mgr._save_sync(checkpoint)

    if cancelled:
        return {"stopped": True, "runId": run_id}

    if checkpoint:
        return {"stopped": True, "runId": run_id, "note": "No active task found, status updated"}

    return JSONResponse({"error": "Run not found"}, status_code=404)


@app.get("/api/report/{run_id}")
async def get_report(run_id: str):
    report = orchestrator.checkpoint_mgr.load_report(run_id)
    if not report:
        return JSONResponse({"error": "Report not found"}, status_code=404)
    return report


@app.get("/api/ingest/status/{benchmark}")
async def get_ingest_status(benchmark: str):
    return orchestrator.get_ingest_status(benchmark)


@app.get("/api/providers")
async def list_providers():
    from adapters import PROVIDER_REGISTRY
    return list(PROVIDER_REGISTRY.keys())


@app.get("/api/benchmarks")
async def list_benchmarks():
    from datasets.base import BENCHMARK_REGISTRY
    return list(BENCHMARK_REGISTRY.keys())


# ══════════════════════════════════════════════════════════════════
# COMPARISONS
# ══════════════════════════════════════════════════════════════════


def _load_comparison(compare_id: str) -> Optional[Dict]:
    path = os.path.join(COMPARISONS_DIR, compare_id, "comparison.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _save_comparison(data: Dict) -> None:
    cid = data["compareId"]
    path = os.path.join(COMPARISONS_DIR, cid, "comparison.json")
    _atomic_write_json(path, data)


@app.post("/api/compare")
async def start_compare(body: Dict[str, Any]):
    providers = body.get("providers", [])
    benchmark = body.get("benchmark", "longmemeval")
    judge = body.get("judge_model", "gpt-4o")
    model = body.get("answering_model", "gpt-4o")
    isolation_mode = body.get("isolation_mode", "global")
    force = body.get("force", False)
    limit = body.get("limit")
    compare_id = body.get("compare_id") or f"compare-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    sampling = SamplingConfig(mode="limit", limit=limit) if limit else None

    task_key = f"compare-{compare_id}"
    if task_key in _running_tasks and not _running_tasks[task_key].done():
        return JSONResponse({"error": "Compare already running"}, status_code=409)

    # Persist comparison metadata
    comp_data = {
        "compareId": compare_id,
        "providers": providers,
        "benchmark": benchmark,
        "judge": judge,
        "answeringModel": model,
        "isolationMode": isolation_mode,
        "status": "running",
        "runs": {},
        "createdAt": _now_iso(),
        "updatedAt": _now_iso(),
    }
    _save_comparison(comp_data)

    async def _run():
        try:
            results = await orchestrator.compare(
                provider_names=providers, benchmark_name=benchmark,
                judge_model=judge, answering_model=model,
                isolation_mode=isolation_mode, sampling=sampling, force=force,
            )
            # Update comparison with run IDs and status
            comp = _load_comparison(compare_id) or comp_data
            for pname, report in results.items():
                if report:
                    comp["runs"][pname] = report.run_id
            comp["status"] = "completed"
            comp["updatedAt"] = _now_iso()
            _save_comparison(comp)
        except Exception as e:
            logger.error("Compare failed: %s", e)
            comp = _load_comparison(compare_id)
            if comp:
                comp["status"] = "failed"
                comp["updatedAt"] = _now_iso()
                _save_comparison(comp)
            await ws_broadcast({"type": "compare_error", "compareId": compare_id, "error": str(e)})

    _running_tasks[task_key] = asyncio.create_task(_run())
    return {"started": True, "compareId": compare_id, "providers": providers, "benchmark": benchmark}


@app.get("/api/compare")
async def list_comparisons():
    if not os.path.exists(COMPARISONS_DIR):
        return []
    results = []
    for name in sorted(os.listdir(COMPARISONS_DIR)):
        comp = _load_comparison(name)
        if comp:
            results.append(comp)
    return results


@app.get("/api/compare/{compare_id}")
async def get_comparison(compare_id: str):
    comp = _load_comparison(compare_id)
    if not comp:
        return JSONResponse({"error": "Comparison not found"}, status_code=404)
    return comp


@app.get("/api/compare/{compare_id}/report")
async def get_comparison_report(compare_id: str):
    comp = _load_comparison(compare_id)
    if not comp:
        return JSONResponse({"error": "Comparison not found"}, status_code=404)

    reports = {}
    for pname, run_id in comp.get("runs", {}).items():
        report = orchestrator.checkpoint_mgr.load_report(run_id)
        if report:
            reports[pname] = report
    return {"compareId": compare_id, "reports": reports}


@app.delete("/api/compare/{compare_id}")
async def delete_comparison(compare_id: str):
    comp_dir = os.path.join(COMPARISONS_DIR, compare_id)
    if not os.path.exists(comp_dir):
        return JSONResponse({"error": "Comparison not found"}, status_code=404)
    shutil.rmtree(comp_dir)
    return {"deleted": True, "compareId": compare_id}


@app.post("/api/compare/{compare_id}/stop")
async def stop_comparison(compare_id: str):
    task_key = f"compare-{compare_id}"
    task = _running_tasks.get(task_key)
    if task and not task.done():
        task.cancel()
        comp = _load_comparison(compare_id)
        if comp:
            comp["status"] = "failed"
            comp["updatedAt"] = _now_iso()
            _save_comparison(comp)
        return {"stopped": True}
    return JSONResponse({"error": "No running comparison found"}, status_code=404)


# ══════════════════════════════════════════════════════════════════
# LEADERBOARD
# ══════════════════════════════════════════════════════════════════


def _load_leaderboard() -> Dict:
    if not os.path.exists(LEADERBOARD_PATH):
        return {"entries": [], "nextId": 1}
    with open(LEADERBOARD_PATH) as f:
        return json.load(f)


def _save_leaderboard(data: Dict) -> None:
    _atomic_write_json(LEADERBOARD_PATH, data)


@app.get("/api/leaderboard")
async def list_leaderboard():
    lb = _load_leaderboard()
    return lb["entries"]


@app.get("/api/leaderboard/{entry_id}")
async def get_leaderboard_entry(entry_id: int):
    lb = _load_leaderboard()
    for entry in lb["entries"]:
        if entry["id"] == entry_id:
            return entry
    return JSONResponse({"error": "Entry not found"}, status_code=404)


@app.post("/api/leaderboard")
async def add_to_leaderboard(body: Dict[str, Any]):
    run_id = body.get("run_id", "")
    version = body.get("version", "1.0")
    notes = body.get("notes", "")

    # Load run report
    report = orchestrator.checkpoint_mgr.load_report(run_id)
    if not report:
        return JSONResponse({"error": "Report not found for run"}, status_code=404)

    checkpoint = orchestrator.checkpoint_mgr.load(run_id)
    if not checkpoint:
        return JSONResponse({"error": "Run not found"}, status_code=404)

    lb = _load_leaderboard()
    entry_id = lb["nextId"]
    lb["nextId"] = entry_id + 1

    entry = {
        "id": entry_id,
        "runId": run_id,
        "provider": report.get("provider", checkpoint.provider),
        "benchmark": report.get("benchmark", checkpoint.benchmark),
        "version": version,
        "accuracy": report.get("summary", {}).get("accuracy", 0),
        "totalQuestions": report.get("summary", {}).get("total_questions", 0),
        "correctCount": report.get("summary", {}).get("correct_count", 0),
        "byQuestionType": report.get("by_question_type", {}),
        "latency": report.get("latency", {}),
        "retrieval": report.get("retrieval"),
        "isolationMode": getattr(checkpoint, "isolation_mode", "global"),
        "judgeModel": report.get("judge", checkpoint.judge),
        "answeringModel": report.get("answering_model", checkpoint.answering_model),
        "addedAt": _now_iso(),
        "notes": notes,
    }

    lb["entries"].append(entry)
    _save_leaderboard(lb)
    return entry


@app.delete("/api/leaderboard/{entry_id}")
async def remove_from_leaderboard(entry_id: int):
    lb = _load_leaderboard()
    lb["entries"] = [e for e in lb["entries"] if e["id"] != entry_id]
    _save_leaderboard(lb)
    return {"deleted": True, "id": entry_id}


# ══════════════════════════════════════════════════════════════════
# WEBSOCKET
# ══════════════════════════════════════════════════════════════════


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    logger.info("WebSocket client connected (%d total)", len(_clients))

    try:
        await ws.send_json({
            "type": "connected",
            "providers": list((await list_providers())),
            "benchmarks": list((await list_benchmarks())),
            "runs": orchestrator.list_runs(),
        })

        while True:
            data = await ws.receive_json()
            action = data.get("action", "")

            if action == "ingest":
                await start_ingest(data)
            elif action == "run":
                await start_run(data)
            elif action == "compare":
                await start_compare(data)
            elif action == "status":
                run_id = data.get("run_id")
                if run_id:
                    status = orchestrator.get_run_status(run_id)
                    await ws.send_json({"type": "status", "run_id": run_id, "data": status})
            elif action == "list_runs":
                await ws.send_json({"type": "runs", "data": orchestrator.list_runs()})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WebSocket error: %s", e)
    finally:
        _clients.discard(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(_clients))


# ══════════════════════════════════════════════════════════════════
# STATIC FILES + ENTRY POINT
# ══════════════════════════════════════════════════════════════════

frontend_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "out")
if not os.path.exists(frontend_dist):
    frontend_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")


def run_server(host: str = "0.0.0.0", port: int = 8766):
    """Start the server."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    orchestrator.set_broadcast(ws_broadcast)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger.info("Starting Context Bench server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
