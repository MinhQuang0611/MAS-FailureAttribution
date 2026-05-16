"""
Bảng tham chiếu: Pipeline Step ↔ Tín hiệu log ↔ Logic detection ↔ Paper alignment.

Dùng cho tài liệu hóa JSON log và notebook; không thay thế trường trong PipelineLogEntry.
"""

from __future__ import annotations

from typing import TypedDict


class PipelineStepRow(TypedDict):
    pipeline_step: str
    required_signals: str
    detection_evaluation: str
    paper_alignment: str


PIPELINE_LOGGING_SPEC: list[PipelineStepRow] = [
    {
        "pipeline_step": "A. Intent Understanding (Intent Parsing & Clarification)",
        "required_signals": "x₁ Raw User Query; x₂ External Knowledge / Constraints; x₃ Clarified Intent / Decomposed Subtasks",
        "detection_evaluation": (
            "Compare x₁ + x₂ ↔ x₃: missing constraints (time, location, condition); "
            "misinterpreted intent; over-inference / hallucinated assumptions; "
            "keyword coverage & semantic consistency"
        ),
        "paper_alignment": "AmbiSQL, SQL-of-Thought",
    },
    {
        "pipeline_step": "B. Schema Linking & Grounding (Schema Selection & Alignment)",
        "required_signals": "x₄ Full Database Schema; x₅ Filtered / Linked Schema; x₆ Token-to-Schema Alignment",
        "detection_evaluation": (
            "Compare x₄ ↔ x₅: missing tables/columns; hallucinated schema elements. "
            "Use x₆: wrong column mapping; ambiguous token alignment"
        ),
        "paper_alignment": "SCoT2S, EMLC, Schema Linking papers",
    },
    {
        "pipeline_step": "C. SQL Generation & Reasoning (Logical Planning & Query Construction)",
        "required_signals": "x₇ Query Plan / Reasoning Trace; x₈ SQL Skeleton; x₉ Candidate SQLs (Pre-SQLs)",
        "detection_evaluation": (
            "Validate x₇: logical correctness (decomposition, aggregation, joins). "
            "Compare x₈ vs required structure: missing clauses (GROUP BY, JOIN, HAVING, …). "
            "Analyze x₉: self-consistency; diversity vs correctness trade-off"
        ),
        "paper_alignment": "SCoT2S, EMLC (Skeleton Error), SEA-SQL",
    },
    {
        "pipeline_step": "D. Execution & Verification (Execution, Feedback & Self-Correction)",
        "required_signals": (
            "x₁₀ Runtime Error Messages; x₁₁ Execution Results; "
            "x₁₂ Failure Analysis / Reflection; x₁₃ Final Repaired SQL"
        ),
        "detection_evaluation": (
            "Combine x₉ + x₁₀ + x₁₁: syntax errors; semantic errors (empty/wrong results). "
            "Compare x₁₂ with error taxonomy: correct vs incorrect diagnosis. "
            "Compare x₁₃ ↔ x₉: verify correction vs new errors introduced"
        ),
        "paper_alignment": "EMLC (Execution Error), SQLFixAgent, SQL-of-Thought",
    },
]


def default_paper_refs_for_step(step: str) -> list[str]:
    """Map bước A/B/C/D → chuỗi tham chiếu (liên kết với PaperRef enum khi cần)."""
    m = {
        "A": ["AmbiSQL", "SQL-of-Thought"],
        "B": ["SCoT2S", "EMLC", "Schema Linking"],
        "C": ["SCoT2S", "EMLC (Skeleton Error)", "SEA-SQL"],
        "D": ["EMLC (Execution Error)", "SQLFixAgent", "SQL-of-Thought"],
    }
    return m.get(step.upper(), [])
