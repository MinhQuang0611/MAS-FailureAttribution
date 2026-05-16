"""
logging_core/log_models.py

Định nghĩa strict Pydantic types cho toàn bộ tín hiệu x1 → x13
theo từng bước trong pipeline T2SQL.

Pipeline Steps:
    A. Intent Understanding      → x1, x2, x3
    B. Schema Linking            → x4, x5, x6
    C. SQL Generation            → x7, x8, x9
    D. Execution & Verification  → x10, x11, x12, x13

Kèm lớp *Detection* (ghi nhận kết quả so khớp / đánh giá) căn theo
Detection & Evaluation Logic + Paper Alignment — xem pipeline_spec.py.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXTRA = "extra"


class PaperRef(str, Enum):
    """Tham chiếu benchmark / paper gắn với từng bước (mục Paper Alignment)."""

    AMBISQL = "AmbiSQL"
    SQL_OF_THOUGHT = "SQL-of-Thought"
    SCOT2S = "SCoT2S"
    EMLC = "EMLC"
    EMLC_SKELETON = "EMLC (Skeleton Error)"
    EMLC_EXECUTION = "EMLC (Execution Error)"
    SCHEMA_LINKING = "Schema Linking"
    SEA_SQL = "SEA-SQL"
    SQLFIXAGENT = "SQLFixAgent"


class RootCauseLabel(str, Enum):
    INTENT_MISSING_CONSTRAINT    = "intent_missing_constraint"
    INTENT_MISINTERPRETED        = "intent_misinterpreted"
    INTENT_HALLUCINATED          = "intent_hallucinated"
    SCHEMA_MISSING_TABLE         = "schema_missing_table"
    SCHEMA_MISSING_COLUMN        = "schema_missing_column"
    SCHEMA_HALLUCINATED_ELEMENT  = "schema_hallucinated_element"
    SCHEMA_WRONG_COLUMN_MAPPING  = "schema_wrong_column_mapping"
    SQL_WRONG_AGGREGATION        = "sql_wrong_aggregation"
    SQL_MISSING_CLAUSE           = "sql_missing_clause"
    SQL_WRONG_JOIN               = "sql_wrong_join"
    EXEC_SYNTAX_ERROR            = "exec_syntax_error"
    EXEC_EMPTY_RESULT            = "exec_empty_result"
    EXEC_WRONG_RESULT            = "exec_wrong_result"
    REPAIR_INTRODUCED_NEW_ERROR  = "repair_introduced_new_error"
    CORRECT                      = "correct"
    UNKNOWN                      = "unknown"


# ---------------------------------------------------------------------------
# Step A: Intent Understanding (x1, x2, x3)
# ---------------------------------------------------------------------------

class X1_RawUserQuery(BaseModel):
    """Câu hỏi ngôn ngữ tự nhiên gốc từ người dùng."""
    question: str
    db_id: str
    difficulty: Optional[Difficulty] = None
    source: Optional[str] = None  # e.g., "spider", "spider_syn", "dr_spider"


class X2_ExternalKnowledge(BaseModel):
    """Ràng buộc / kiến thức ngoài được đưa vào (nếu có)."""
    constraints: List[str] = Field(default_factory=list)
    domain_hints: List[str] = Field(default_factory=list)
    raw_context: Optional[str] = None


class X3_ClarifiedIntent(BaseModel):
    """Intent đã được làm rõ và phân rã thành các subtask."""
    clarified_question: str
    subtasks: List[str] = Field(default_factory=list)
    detected_conditions: List[str] = Field(default_factory=list)
    is_ambiguous: bool = False
    clarification_turns: int = 0


# ---------------------------------------------------------------------------
# Detection & evaluation (so khớp tín hiệu — điền sau khi chạy logic / gán nhãn)
# ---------------------------------------------------------------------------

class StepADetection(BaseModel):
    """
    A. Intent — so x₁ + x₂ ↔ x₃:
    missing constraints, misinterpreted intent, over-inference / hallucinated assumptions;
    keyword coverage & semantic consistency.
    """

    missing_constraints: Optional[bool] = None
    misinterpreted_intent: Optional[bool] = None
    over_inference_or_hallucinated_assumptions: Optional[bool] = None
    keyword_coverage_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    semantic_consistency_ok: Optional[bool] = None
    notes: List[str] = Field(default_factory=list)


class StepBDetection(BaseModel):
    """
    B. Schema — so x₄ ↔ x₅ và x₆:
    missing tables/columns, hallucinated schema; wrong mapping / ambiguous alignment.
    """

    missing_tables_or_columns: Optional[bool] = None
    hallucinated_schema_elements: Optional[bool] = None
    wrong_column_mapping: Optional[bool] = None
    ambiguous_token_alignment: Optional[bool] = None
    notes: List[str] = Field(default_factory=list)


class StepCDetection(BaseModel):
    """
    C. SQL Gen — x₇ plan, x₈ skeleton, x₉ candidates:
    plan correctness; skeleton structure vs required clauses; self-consistency & diversity trade-off.
    """

    plan_logical_valid: Optional[bool] = None
    plan_issues: List[str] = Field(default_factory=list)
    skeleton_structure_ok: Optional[bool] = None
    skeleton_missing_clauses: List[str] = Field(default_factory=list)
    candidates_self_consistency_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    candidates_diversity_vs_correctness_notes: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


class StepDDetection(BaseModel):
    """
    D. Execution — kết hợp x₉ + x₁₀ + x₁₁; x₁₂ taxonomy; x₁₃ vs x₉:
    syntax / semantic errors; diagnosis quality; repair vs regression.
    """

    syntax_error_detected: Optional[bool] = None
    semantic_error_empty_or_wrong_result: Optional[bool] = None
    x12_diagnosis_matches_error_taxonomy: Optional[bool] = None
    x13_correction_verified_no_new_errors: Optional[bool] = None
    notes: List[str] = Field(default_factory=list)


class PipelineDetectionBundle(BaseModel):
    """Gói kết quả detection theo 4 bước pipeline (tùy chọn từng bước)."""

    step_a: Optional[StepADetection] = None
    step_b: Optional[StepBDetection] = None
    step_c: Optional[StepCDetection] = None
    step_d: Optional[StepDDetection] = None


# ---------------------------------------------------------------------------
# Step B: Schema Linking & Grounding (x4, x5, x6)
# ---------------------------------------------------------------------------

class ColumnInfo(BaseModel):
    name: str
    dtype: Optional[str] = None

class TableInfo(BaseModel):
    table_name: str
    columns: List[ColumnInfo] = Field(default_factory=list)


class X4_FullSchema(BaseModel):
    """Toàn bộ schema của database."""
    db_id: str
    tables: List[TableInfo] = Field(default_factory=list)


class X5_FilteredSchema(BaseModel):
    """Schema đã được lọc — chỉ giữ lại bảng/cột liên quan."""
    db_id: str
    selected_tables: List[TableInfo] = Field(default_factory=list)
    pruning_rationale: Optional[str] = None


class TokenAlignment(BaseModel):
    token: str
    mapped_table: Optional[str] = None
    mapped_column: Optional[str] = None
    confidence: float = 1.0
    is_hallucinated: bool = False


class X6_SchemaAlignment(BaseModel):
    """Ánh xạ từng token trong câu hỏi sang schema elements."""
    alignments: List[TokenAlignment] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Step C: SQL Generation & Reasoning (x7, x8, x9)
# ---------------------------------------------------------------------------

class X7_QueryPlan(BaseModel):
    """Kế hoạch truy vấn / chuỗi suy luận (reasoning trace)."""
    steps: List[str] = Field(default_factory=list)
    reasoning_trace_raw: Optional[str] = None
    uses_aggregation: bool = False
    uses_join: bool = False
    uses_subquery: bool = False
    uses_having: bool = False


class X8_SQLSkeleton(BaseModel):
    """Skeleton SQL — cấu trúc câu truy vấn chưa điền giá trị cụ thể."""
    skeleton: str  # e.g., "SELECT _ FROM _ WHERE _ GROUP BY _"
    expected_clauses: List[str] = Field(default_factory=list)


class X9_CandidateSQLs(BaseModel):
    """Danh sách các SQL ứng viên (Pre-SQLs) được sinh ra."""
    candidates: List[str] = Field(default_factory=list)
    selected_index: Optional[int] = None   # index sau self-consistency
    selected_sql: Optional[str] = None
    diversity_metrics: Optional[str] = None  # ghi chú nhanh về độ đa dạng ứng viên


# ---------------------------------------------------------------------------
# Step D: Execution & Verification (x10, x11, x12, x13)
# ---------------------------------------------------------------------------

class X10_RuntimeError(BaseModel):
    """Lỗi runtime khi thực thi SQL."""
    has_error: bool = False
    error_type: Optional[str] = None   # e.g., "OperationalError", "SyntaxError"
    error_message: Optional[str] = None
    error_taxonomy_tag: Optional[str] = None  # gắn với taxonomy trong paper / EMLC


class X11_ExecutionResult(BaseModel):
    """Kết quả thực thi SQL."""
    rows: List[Any] = Field(default_factory=list)
    row_count: int = 0
    is_empty: bool = True
    execution_time_ms: Optional[float] = None
    result_semantically_wrong: Optional[bool] = None  # non-empty nhưng sai ngữ nghĩa so với gold


class X12_FailureAnalysis(BaseModel):
    """Phân tích lỗi / reflection sau khi thực thi."""
    predicted_root_cause: RootCauseLabel = RootCauseLabel.UNKNOWN
    explanation: Optional[str] = None
    suggested_fix: Optional[str] = None
    reflection_trace: Optional[str] = None
    taxonomy_label: Optional[str] = None  # so khớp error taxonomy (paper)


class X13_RepairedSQL(BaseModel):
    """SQL đã được sửa sau khi phân tích lỗi."""
    repaired_sql: Optional[str] = None
    repair_applied: bool = False
    introduced_new_error: bool = False


# ---------------------------------------------------------------------------
# Master Log Entry: tổng hợp toàn bộ x1→x13 cho 1 sample
# ---------------------------------------------------------------------------

class PipelineLogEntry(BaseModel):
    """
    Log entry đầy đủ cho một câu hỏi chạy qua pipeline.
    Chứa toàn bộ tín hiệu x1 → x13 và metadata.
    """
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    # Metadata
    entry_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    model_name: str
    gold_sql: Optional[str] = None

    # Step A
    x1: Optional[X1_RawUserQuery]      = None
    x2: Optional[X2_ExternalKnowledge] = None
    x3: Optional[X3_ClarifiedIntent]   = None

    # Step B
    x4: Optional[X4_FullSchema]        = None
    x5: Optional[X5_FilteredSchema]    = None
    x6: Optional[X6_SchemaAlignment]   = None

    # Step C
    x7: Optional[X7_QueryPlan]         = None
    x8: Optional[X8_SQLSkeleton]       = None
    x9: Optional[X9_CandidateSQLs]     = None

    # Step D
    x10: Optional[X10_RuntimeError]    = None
    x11: Optional[X11_ExecutionResult] = None
    x12: Optional[X12_FailureAnalysis] = None
    x13: Optional[X13_RepairedSQL]     = None

    # Detection & evaluation (điền sau so khớp / rule / nhãn thủ công)
    detection: Optional[PipelineDetectionBundle] = None

    # Evaluation
    final_sql: Optional[str]           = None
    execution_match: Optional[bool]    = None
    exact_match: Optional[bool]        = None
    annotated_root_cause: Optional[RootCauseLabel] = None
