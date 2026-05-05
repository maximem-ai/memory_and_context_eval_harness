"""
Unified type system for the benchmarker pipeline.

All dataclasses and type definitions used across providers, benchmarks,
checkpoints, and the orchestrator pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Union


# ── Data Models ────────────────────────────────────────────────────────


@dataclass
class UnifiedMessage:
    role: Literal["user", "assistant"]
    content: str
    timestamp: Optional[str] = None
    speaker: Optional[str] = None


@dataclass
class UnifiedSession:
    session_id: str
    messages: List[UnifiedMessage]
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class UnifiedQuestion:
    question_id: str
    question: str
    question_type: str
    ground_truth: str
    haystack_session_ids: List[str]
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class QuestionTypeInfo:
    id: str
    alias: str
    description: str


QuestionTypeRegistry = Dict[str, QuestionTypeInfo]


# ── Provider I/O ───────────────────────────────────────────────────────


@dataclass
class ProviderConfig:
    api_key: str = ""
    base_url: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "api_key":
            return self.api_key
        if key == "base_url":
            return self.base_url
        return self.extras.get(key, default)


@dataclass
class IngestOptions:
    container_tag: str
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class SearchOptions:
    container_tag: str
    limit: Optional[int] = None
    threshold: Optional[float] = None
    on_request_log: Optional[Callable[["ProviderRequestLog"], None]] = None


@dataclass
class ProviderRequestLog:
    provider: str
    action: str
    method: Optional[str] = None
    url: Optional[str] = None
    request: Optional[Dict[str, Any]] = None
    response: Optional[Dict[str, Any]] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[float] = None
    status: Optional[Literal["ok", "error"]] = None
    error: Optional[str] = None


SessionIngestionStatus = Literal[
    "queued", "processing", "completed", "failed",
    "unknown_completed", "polling_retry",
]


@dataclass
class SessionIngestion:
    session_id: str
    ingestion_id: Optional[str] = None
    status: SessionIngestionStatus = "queued"
    turn_count: Optional[int] = None
    queued_at: Optional[str] = None
    completed_at: Optional[str] = None
    memories_created: Optional[int] = None
    batch_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class IngestionStatus:
    status: str
    memories_created: int = 0
    memory_ids: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class IngestResult:
    document_ids: List[str] = field(default_factory=list)
    task_ids: Optional[List[str]] = None
    session_ingestions: Optional[List[SessionIngestion]] = None


@dataclass
class IndexingProgress:
    completed_ids: List[str]
    failed_ids: List[str]
    total: int


IndexingProgressCallback = Callable[[IndexingProgress], None]


# ── Provider Prompts ───────────────────────────────────────────────────

JudgePromptFunction = Callable[[str, str, str], Dict[str, str]]


@dataclass
class ProviderPrompts:
    answer_prompt: Optional[
        Union[str, Callable[[str, list, Optional[str]], str]]
    ] = None
    judge_prompt: Optional[JudgePromptFunction] = None


# ── Concurrency ────────────────────────────────────────────────────────

PhaseId = Literal["search", "answer", "evaluate", "report"]
PHASE_ORDER: List[PhaseId] = ["search", "answer", "evaluate", "report"]


@dataclass
class ConcurrencyConfig:
    default: int = 1
    search: Optional[int] = None
    answer: Optional[int] = None
    evaluate: Optional[int] = None


def resolve_concurrency(
    phase: PhaseId,
    cli_config: Optional[ConcurrencyConfig] = None,
    provider_default: Optional[ConcurrencyConfig] = None,
) -> int:
    """Resolve concurrency for a phase with priority: CLI phase > CLI default > provider phase > provider default > 1."""
    if cli_config:
        phase_val = getattr(cli_config, phase, None)
        if phase_val is not None:
            return phase_val
        if cli_config.default is not None:
            return cli_config.default

    if provider_default:
        phase_val = getattr(provider_default, phase, None)
        if phase_val is not None:
            return phase_val
        if provider_default.default is not None:
            return provider_default.default

    return 1


def get_phases_from_phase(from_phase: PhaseId) -> List[PhaseId]:
    """Return phases from the given phase onward."""
    try:
        idx = PHASE_ORDER.index(from_phase)
        return PHASE_ORDER[idx:]
    except ValueError:
        return list(PHASE_ORDER)


# ── Checkpoint Types ───────────────────────────────────────────────────

PhaseStatus = Literal["pending", "in_progress", "completed", "failed"]


@dataclass
class SearchPhaseCheckpoint:
    status: PhaseStatus = "pending"
    result_file: Optional[str] = None
    results: Optional[List[Any]] = None
    request_logs: Optional[List[Dict[str, Any]]] = None
    result_count: Optional[int] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[float] = None
    error: Optional[str] = None


@dataclass
class AnswerPhaseCheckpoint:
    status: PhaseStatus = "pending"
    hypothesis: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[float] = None
    error: Optional[str] = None


@dataclass
class EvaluatePhaseCheckpoint:
    status: PhaseStatus = "pending"
    label: Optional[Literal["correct", "incorrect"]] = None
    score: Optional[float] = None
    explanation: Optional[str] = None
    retrieval_metrics: Optional["RetrievalMetrics"] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[float] = None
    error: Optional[str] = None


@dataclass
class QuestionCheckpoint:
    question_id: str
    container_tag: str
    question: str
    ground_truth: str
    question_type: str
    question_date: Optional[str] = None
    phases: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.phases is None:
            self.phases = {
                "search": SearchPhaseCheckpoint(),
                "answer": AnswerPhaseCheckpoint(),
                "evaluate": EvaluatePhaseCheckpoint(),
            }


RunStatus = Literal["initializing", "running", "completed", "failed"]
IsolationMode = Literal["global", "isolated"]
SelectionMode = Literal["full", "sample", "limit", "record", "curated", "interleaved"]
SampleType = Literal["consecutive", "random"]


@dataclass
class SamplingConfig:
    mode: SelectionMode = "full"
    sample_type: Optional[SampleType] = None
    per_category: Optional[int] = None
    limit: Optional[int] = None
    records: Optional[int] = None
    sessions_per_question: Optional[int] = None
    max_per_group: Optional[int] = None  # First N questions per haystack group (record for Locomo/DMR)
    per_record_per_category: Optional[int] = None  # First N questions per (group, question_type) cell — strict 2D stratification
    groups: Optional[List[str]] = None  # Restrict to specific haystack-group ids (e.g. ["locomo_1"])


@dataclass
class RunCheckpoint:
    run_id: str
    global_container_tag: str
    isolation_mode: IsolationMode = "global"
    status: RunStatus = "initializing"
    provider: str = ""
    benchmark: str = ""
    judge: str = ""
    answering_model: str = ""
    created_at: str = ""
    updated_at: str = ""
    limit: Optional[int] = None
    sampling: Optional[SamplingConfig] = None
    target_question_ids: Optional[List[str]] = None
    concurrency: Optional[ConcurrencyConfig] = None
    provider_config: Optional[Dict[str, Any]] = None  # captures retrieval_mode, retrieval_max_results, etc.
    questions: Dict[str, QuestionCheckpoint] = field(default_factory=dict)


# ── Ingest Checkpoints ─────────────────────────────────────────────────


@dataclass
class SessionIngestionRecord:
    session_id: str
    ingestion_id: Optional[str] = None
    status: SessionIngestionStatus = "queued"
    turn_count: Optional[int] = None
    queued_at: Optional[str] = None
    completed_at: Optional[str] = None
    memories_created: Optional[int] = None
    batch_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ProviderIngestState:
    completed_turn_count: int = 0
    completed_turn_ids: List[str] = field(default_factory=list)
    questions_covered: List[str] = field(default_factory=list)
    container_tag: str = ""
    last_updated: str = ""
    session_ingestions: Optional[List[SessionIngestionRecord]] = None


@dataclass
class GlobalIngestCheckpoint:
    dataset: str = ""
    created_at: str = ""
    updated_at: str = ""
    providers: Dict[str, ProviderIngestState] = field(default_factory=dict)
    total_sessions_ingested: int = 0
    total_turns_ingested: int = 0


# ── Metrics & Report ───────────────────────────────────────────────────


@dataclass
class RetrievalMetrics:
    hit_at_k: float = 0.0
    precision_at_k: float = 0.0
    recall_at_k: float = 0.0
    f1_at_k: float = 0.0
    mrr: float = 0.0
    ndcg: float = 0.0
    k: int = 10
    relevant_retrieved: int = 0
    total_relevant: int = 0


@dataclass
class RetrievalAggregates:
    hit_at_k: float = 0.0
    precision_at_k: float = 0.0
    recall_at_k: float = 0.0
    f1_at_k: float = 0.0
    mrr: float = 0.0
    ndcg: float = 0.0
    k: int = 10


@dataclass
class LatencyStats:
    min: float = 0.0
    max: float = 0.0
    mean: float = 0.0
    median: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    std_dev: float = 0.0
    count: int = 0


@dataclass
class QuestionTypeStats:
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0
    latency: Optional[Dict[str, LatencyStats]] = None
    retrieval: Optional[RetrievalAggregates] = None


@dataclass
class EvaluationResult:
    question_id: str = ""
    question_type: str = ""
    question: str = ""
    score: float = 0.0
    label: Literal["correct", "incorrect"] = "incorrect"
    explanation: str = ""
    hypothesis: str = ""
    ground_truth: str = ""
    search_results: List[Any] = field(default_factory=list)
    search_duration_ms: float = 0.0
    answer_duration_ms: float = 0.0
    total_duration_ms: float = 0.0
    retrieval_metrics: Optional[RetrievalMetrics] = None


@dataclass
class BenchmarkResultSummary:
    total_questions: int = 0
    correct_count: int = 0
    accuracy: float = 0.0


@dataclass
class BenchmarkResult:
    provider: str = ""
    benchmark: str = ""
    run_id: str = ""
    global_container_tag: str = ""
    judge: str = ""
    answering_model: str = ""
    timestamp: str = ""
    summary: BenchmarkResultSummary = field(default_factory=BenchmarkResultSummary)
    latency: Optional[Dict[str, LatencyStats]] = None
    retrieval: Optional[RetrievalAggregates] = None
    by_question_type: Dict[str, QuestionTypeStats] = field(default_factory=dict)
    question_type_registry: Optional[QuestionTypeRegistry] = None
    evaluations: List[EvaluationResult] = field(default_factory=list)


# ── Benchmark Interface ────────────────────────────────────────────────


@dataclass
class BenchmarkConfig:
    data_path: Optional[str] = None
    interleaved_path: Optional[str] = None
    top_sessions_per_question: Optional[int] = None


@dataclass
class QuestionFilter:
    question_types: Optional[List[str]] = None
    limit: Optional[int] = None
    offset: Optional[int] = None


class Benchmark(ABC):
    """Abstract base class for all benchmark datasets."""

    name: str

    @abstractmethod
    async def load(self, config: Optional[BenchmarkConfig] = None) -> None:
        ...

    @abstractmethod
    def get_questions(self, filter: Optional[QuestionFilter] = None) -> List[UnifiedQuestion]:
        ...

    @abstractmethod
    def get_haystack_sessions(self, question_id: str) -> List[UnifiedSession]:
        ...

    @abstractmethod
    def get_ground_truth(self, question_id: str) -> str:
        ...

    @abstractmethod
    def get_question_types(self) -> QuestionTypeRegistry:
        ...

    def get_question_group_id(self, question_id: str) -> str:
        """Group questions that share the same haystack so isolated mode
        ingests once per group, not once per question. Default: per-question
        (one group per question, suitable when haystacks are independent like
        LongMemEval). Override for benchmarks where multiple questions share
        a haystack (Locomo, DMR — group by record prefix).
        """
        return question_id


# ── Provider Interface ─────────────────────────────────────────────────

ProviderName = Literal["synap", "mem0", "zep", "supermemory"]


class Provider(ABC):
    """Abstract base class for all memory system providers."""

    name: str
    prompts: Optional[ProviderPrompts] = None
    concurrency: Optional[ConcurrencyConfig] = None

    @abstractmethod
    async def initialize(self, config: ProviderConfig) -> None:
        ...

    @abstractmethod
    async def ingest(
        self, sessions: List[UnifiedSession], options: IngestOptions
    ) -> IngestResult:
        ...

    @abstractmethod
    async def await_indexing(
        self,
        result: IngestResult,
        container_tag: str,
        on_progress: Optional[IndexingProgressCallback] = None,
    ) -> None:
        ...

    @abstractmethod
    async def search(self, query: str, options: SearchOptions) -> List[Any]:
        ...

    @abstractmethod
    async def clear(self, container_tag: str) -> None:
        ...

    # Optional async ingestion (fire-and-forget + polling)
    async def ingest_fire_and_forget(
        self, sessions: List[UnifiedSession], options: IngestOptions
    ) -> IngestResult:
        raise NotImplementedError

    async def check_ingestion_status(self, ingestion_id: str) -> IngestionStatus:
        raise NotImplementedError


# ── Helper ─────────────────────────────────────────────────────────────


def build_context_string(context: list) -> str:
    """Serialize context to a JSON string for prompt building."""
    import json
    return json.dumps(context, indent=2, default=str)
