"""
Orchestrator — coordinates the 5-phase benchmark pipeline.

Pipeline: Global Ingest → Global Indexing → Search → Answer → Evaluate → Report

Supports:
- Single provider runs
- Multi-provider comparison (compare command)
- Phase-level control (run specific phases)
- Resumable from any phase via checkpoints
"""

import asyncio
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from adapters import create_provider
from datasets.base import create_benchmark
from runner.checkpoint import CheckpointManager
from runner.ingest_checkpoint import GlobalIngestCheckpointManager
from runner.types import (
    Benchmark,
    BenchmarkResult,
    ConcurrencyConfig,
    IsolationMode,
    PhaseId,
    Provider,
    ProviderConfig,
    RunCheckpoint,
    SamplingConfig,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_run_id(provider: str, benchmark: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{provider}-{benchmark}-{ts}"


class Orchestrator:
    """Main orchestrator coordinating the benchmark pipeline."""

    def __init__(self):
        self.checkpoint_mgr = CheckpointManager()
        self.ingest_mgr = GlobalIngestCheckpointManager()
        self._broadcast_fn: Optional[Callable] = None
        self._active_ingests: Dict[str, bool] = {}  # "provider-benchmark" -> True while running

    def set_broadcast(self, broadcast_fn: Callable) -> None:
        """Set the WebSocket broadcast function."""
        self._broadcast_fn = broadcast_fn

    def _broadcast(self, data: Dict[str, Any]) -> None:
        """Broadcast data to WebSocket clients (handles async broadcast fn)."""
        if not self._broadcast_fn:
            return
        import inspect
        result = self._broadcast_fn(data)
        if inspect.iscoroutine(result):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(result)
            except RuntimeError:
                pass

    # ── Provider + Benchmark setup ────────────────────────────────────

    async def _init_provider(self, name: str, config: Optional[Dict[str, Any]] = None) -> Provider:
        provider = create_provider(name)
        provider_config = self._load_provider_config(name, config)
        await provider.initialize(provider_config)
        return provider

    def _load_provider_config(self, name: str, overrides: Optional[Dict[str, Any]] = None) -> ProviderConfig:
        """Load provider config from YAML file with env var resolution."""
        import yaml
        config_path = os.path.join("configs", f"{name}.yaml")
        extras: Dict[str, Any] = {}

        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                raw = yaml.safe_load(f) or {}
            for k, v in raw.items():
                if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                    env_var = v[2:-1]
                    extras[k] = os.environ.get(env_var, "")
                else:
                    extras[k] = v

        if overrides:
            extras.update(overrides)

        return ProviderConfig(
            api_key=extras.pop("api_key", ""),
            base_url=extras.pop("base_url", None),
            extras=extras,
        )

    async def _init_benchmark(self, name: str) -> Benchmark:
        benchmark = create_benchmark(name)
        await benchmark.load()
        return benchmark

    # ── Global Ingest ─────────────────────────────────────────────────

    async def global_ingest(
        self,
        provider_name: str,
        benchmark_name: str,
        isolation_mode: IsolationMode = "global",
        provider_config: Optional[Dict[str, Any]] = None,
        container_tag_prefix: Optional[str] = None,
        on_progress: Optional[Callable] = None,
    ) -> str:
        """Ingest benchmark data into a provider. Returns container_tag (or sentinel for isolated)."""
        ingest_key = f"{provider_name}-{benchmark_name}"
        if self._active_ingests.get(ingest_key):
            logger.info("[%s] Ingestion already in progress for %s, skipping", provider_name, benchmark_name)
            return container_tag_prefix or f"{benchmark_name}-{provider_name}"

        self._active_ingests[ingest_key] = True
        try:
            return await self._do_ingest(provider_name, benchmark_name, isolation_mode, provider_config, container_tag_prefix, on_progress)
        finally:
            self._active_ingests.pop(ingest_key, None)

    async def _do_ingest(
        self,
        provider_name: str,
        benchmark_name: str,
        isolation_mode: IsolationMode = "global",
        provider_config: Optional[Dict[str, Any]] = None,
        container_tag_prefix: Optional[str] = None,
        on_progress: Optional[Callable] = None,
    ) -> str:
        provider = await self._init_provider(provider_name, provider_config)
        benchmark = await self._init_benchmark(benchmark_name)

        container_tag = container_tag_prefix or f"{benchmark_name}-{provider_name}"
        checkpoint = self.ingest_mgr.get_or_create(benchmark_name)

        self._broadcast({
            "type": "ingest_start",
            "provider": provider_name,
            "benchmark": benchmark_name,
            "container_tag": container_tag,
            "isolation_mode": isolation_mode,
        })

        if isolation_mode == "isolated":
            from runner.phases.global_ingest import run_isolated_ingest
            await run_isolated_ingest(
                provider, benchmark, provider_name, benchmark_name,
                checkpoint, self.ingest_mgr,
                container_tag_prefix=container_tag_prefix,
                on_progress=on_progress or self._ingest_progress,
            )
        else:
            from runner.phases.global_ingest import run_global_ingest
            await run_global_ingest(
                provider, benchmark, container_tag,
                checkpoint, self.ingest_mgr,
                on_progress=on_progress or self._ingest_progress,
            )

            from runner.phases.global_indexing import run_global_indexing
            await run_global_indexing(
                provider, checkpoint, self.ingest_mgr,
                on_progress=on_progress or self._ingest_progress,
            )

        self._broadcast({
            "type": "ingest_complete",
            "provider": provider_name,
            "benchmark": benchmark_name,
            "container_tag": container_tag,
            "isolation_mode": isolation_mode,
        })

        return container_tag

    # ── Run (Search → Answer → Evaluate → Report) ────────────────────

    async def run(
        self,
        provider_name: str,
        benchmark_name: str,
        judge_model: str = "gpt-4o",
        answering_model: str = "gpt-4o",
        run_id: Optional[str] = None,
        isolation_mode: IsolationMode = "global",
        sampling: Optional[SamplingConfig] = None,
        concurrency: Optional[ConcurrencyConfig] = None,
        phases: Optional[List[PhaseId]] = None,
        system_prompt: str = "",
        provider_config: Optional[Dict[str, Any]] = None,
        container_tag_prefix: Optional[str] = None,
        force: bool = False,
        on_progress: Optional[Callable] = None,
    ) -> BenchmarkResult:
        """Run eval pipeline: search → answer → evaluate → report."""
        provider = await self._init_provider(provider_name, provider_config)
        benchmark = await self._init_benchmark(benchmark_name)

        container_tag = container_tag_prefix or f"{benchmark_name}-{provider_name}"
        run_id = run_id or _generate_run_id(provider_name, benchmark_name)
        phases = phases or ["search", "answer", "evaluate", "report"]
        progress_fn = on_progress or self._eval_progress

        # Load or create checkpoint
        checkpoint = self.checkpoint_mgr.load(run_id)
        if checkpoint and force:
            checkpoint = None

        if not checkpoint:
            checkpoint = self.checkpoint_mgr.create(
                run_id=run_id,
                provider=provider_name,
                benchmark=benchmark_name,
                global_container_tag=container_tag,
                judge=judge_model,
                answering_model=answering_model,
                isolation_mode=isolation_mode,
                sampling=sampling,
                concurrency=concurrency,
            )

        # Initialize questions in checkpoint
        questions = benchmark.get_questions()
        if sampling and sampling.limit:
            questions = questions[:sampling.limit]

        for q in questions:
            # In isolated mode, each question gets its own container
            if isolation_mode == "isolated":
                q_container_tag = f"{container_tag}_{q.question_id}"
            else:
                q_container_tag = container_tag

            self.checkpoint_mgr.init_question(
                checkpoint, q.question_id, q_container_tag,
                q.question, q.ground_truth, q.question_type,
                q.metadata.get("question_date") if q.metadata else None,
            )

        checkpoint.status = "running"
        checkpoint.judge = judge_model
        checkpoint.answering_model = answering_model
        await self.checkpoint_mgr.save(checkpoint)

        self._broadcast({
            "type": "run_start",
            "run_id": run_id,
            "provider": provider_name,
            "benchmark": benchmark_name,
            "total_questions": len(checkpoint.questions),
            "phases": phases,
        })

        # Execute phases
        if "search" in phases:
            from runner.phases.search import run_search_phase
            await run_search_phase(provider, benchmark, checkpoint, self.checkpoint_mgr, progress_fn)

        if "answer" in phases:
            from runner.phases.answer import run_answer_phase
            await run_answer_phase(
                benchmark, checkpoint, self.checkpoint_mgr,
                answering_model, system_prompt, provider, progress_fn,
            )

        if "evaluate" in phases:
            from runner.phases.evaluate import run_evaluate_phase
            await run_evaluate_phase(
                benchmark, checkpoint, self.checkpoint_mgr,
                judge_model, provider, progress_fn,
            )

        report = None
        if "report" in phases:
            from runner.phases.report import generate_report
            report = generate_report(benchmark, checkpoint, self.checkpoint_mgr)
            self.checkpoint_mgr.save_report(run_id, asdict(report))

        checkpoint.status = "completed"
        await self.checkpoint_mgr.save(checkpoint)

        self._broadcast({
            "type": "run_complete",
            "run_id": run_id,
            "provider": provider_name,
            "benchmark": benchmark_name,
            "summary": asdict(report.summary) if report else None,
        })

        return report

    # ── Compare (Multi-provider) ──────────────────────────────────────

    async def compare(
        self,
        provider_names: List[str],
        benchmark_name: str,
        judge_model: str = "gpt-4o",
        answering_model: str = "gpt-4o",
        isolation_mode: IsolationMode = "global",
        sampling: Optional[SamplingConfig] = None,
        concurrency: Optional[ConcurrencyConfig] = None,
        system_prompt: str = "",
        force: bool = False,
    ) -> Dict[str, BenchmarkResult]:
        """Run eval across multiple providers for comparison."""
        results: Dict[str, BenchmarkResult] = {}

        self._broadcast({
            "type": "compare_start",
            "providers": provider_names,
            "benchmark": benchmark_name,
            "isolation_mode": isolation_mode,
        })

        # Run providers concurrently
        async def _run_one(pname: str) -> Optional[BenchmarkResult]:
            try:
                return await self.run(
                    provider_name=pname,
                    benchmark_name=benchmark_name,
                    judge_model=judge_model,
                    answering_model=answering_model,
                    isolation_mode=isolation_mode,
                    sampling=sampling,
                    concurrency=concurrency,
                    system_prompt=system_prompt,
                    force=force,
                )
            except Exception as e:
                logger.error("[compare] Provider %s failed: %s", pname, e)
                return None

        gathered = await asyncio.gather(*[_run_one(p) for p in provider_names])
        for pname, result in zip(provider_names, gathered):
            if result:
                results[pname] = result

        self._broadcast({
            "type": "compare_complete",
            "providers": provider_names,
            "benchmark": benchmark_name,
            "results": {
                pname: asdict(r.summary) if r else None
                for pname, r in results.items()
            },
        })

        return results

    # ── Status ────────────────────────────────────────────────────────

    def get_run_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        checkpoint = self.checkpoint_mgr.load(run_id)
        if not checkpoint:
            return None
        return self.checkpoint_mgr.get_summary(checkpoint)

    def get_ingest_status(self, benchmark_name: str) -> Dict[str, Any]:
        checkpoint = self.ingest_mgr.get_or_create(benchmark_name)
        return {
            "dataset": checkpoint.dataset,
            "providers": {
                pname: {
                    "container_tag": pstate.container_tag,
                    "completed_turns": pstate.completed_turn_count,
                    "questions_covered": len(pstate.questions_covered),
                    "in_flight": len(self.ingest_mgr.get_in_flight_sessions(checkpoint, pname)),
                }
                for pname, pstate in checkpoint.providers.items()
            },
        }

    def list_runs(self) -> List[Dict[str, Any]]:
        return self.checkpoint_mgr.list_runs()

    # ── Progress callbacks ────────────────────────────────────────────

    def _ingest_progress(self, data: Dict[str, Any]) -> None:
        self._broadcast({"type": "ingest_progress", **data})

    def _eval_progress(self, data: Dict[str, Any]) -> None:
        self._broadcast({"type": "eval_progress", **data})
