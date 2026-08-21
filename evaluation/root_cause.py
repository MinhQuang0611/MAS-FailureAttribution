"""
evaluation/root_cause.py

Rule-based Root Cause Classifier.
Tự động đoán bước nào gây ra lỗi dựa trên các tín hiệu x1→x13.
"""

from __future__ import annotations

from typing import Tuple

from logging_core.log_models import PipelineLogEntry, RootCauseLabel


class RootCauseClassifier:
    """
    Phân loại nguyên nhân lỗi theo rule-based logic.
    Ưu tiên từ Step D → C → B → A (downstream first).
    """

    def classify(self, entry: PipelineLogEntry) -> Tuple[RootCauseLabel, str]:
        """
        Returns:
            (RootCauseLabel, explanation_string)
        """
        # ----- Nếu đúng thì trả về CORRECT -----
        if entry.execution_match:
            return RootCauseLabel.CORRECT, "Execution result matches gold SQL."

        # ----- Step D: Execution & Verification -----
        if entry.x10 and entry.x10.has_error:
            err_msg = (entry.x10.error_message or "").lower()
            if "no such column" in err_msg or "no such table" in err_msg or "does not exist" in err_msg:
                return (
                    RootCauseLabel.SCHEMA_HALLUCINATED_ELEMENT,
                    f"Hallucinated schema element detected during execution: {entry.x10.error_message}"
                )
            return (
                RootCauseLabel.EXEC_SYNTAX_ERROR,
                f"Runtime error: {entry.x10.error_message}",
            )

        if entry.x11 and entry.x11.is_empty and not entry.x10:
            return (
                RootCauseLabel.EXEC_EMPTY_RESULT,
                "SQL executed without error but returned empty result.",
            )

        if entry.x13 and entry.x13.introduced_new_error:
            return (
                RootCauseLabel.REPAIR_INTRODUCED_NEW_ERROR,
                "Repair step introduced a new error instead of fixing the original.",
            )

        # ----- Step C: SQL Generation -----
        if entry.x8 and entry.x9:
            skeleton = entry.x8.skeleton.upper()
            final = (entry.x9.selected_sql or "").upper()
            missing_clauses = [
                clause for clause in entry.x8.expected_clauses
                if clause.upper() not in final
            ]
            if missing_clauses:
                return (
                    RootCauseLabel.SQL_MISSING_CLAUSE,
                    f"Missing clauses in generated SQL: {missing_clauses}",
                )

        if entry.x7:
            if entry.x7.uses_join and entry.x9:
                sql = (entry.x9.selected_sql or "").upper()
                if "JOIN" not in sql:
                    return (
                        RootCauseLabel.SQL_WRONG_JOIN,
                        "Query plan requires JOIN but generated SQL missing it.",
                    )

        # ----- Step B: Schema Linking -----
        if entry.x6:
            hallucinated = [a for a in entry.x6.alignments if a.is_hallucinated]
            if hallucinated:
                tokens = [a.token for a in hallucinated]
                return (
                    RootCauseLabel.SCHEMA_HALLUCINATED_ELEMENT,
                    f"Hallucinated schema tokens: {tokens}",
                )

        if entry.x4 and entry.x5:
            all_tables = {t.table_name for t in entry.x4.tables}
            selected_tables = {t.table_name for t in entry.x5.selected_tables}
            missing = all_tables - selected_tables
            # Chỉ flag nếu bảng bị bỏ sót và xuất hiện trong gold SQL
            if entry.gold_sql and missing:
                import re
                missing_in_gold = []
                gold_lower = (entry.gold_sql or "").lower()
                for t in missing:
                    if re.search(rf"\b{re.escape(t.lower())}\b", gold_lower):
                        missing_in_gold.append(t)
                if missing_in_gold:
                    return (
                        RootCauseLabel.SCHEMA_MISSING_TABLE,
                        f"Tables in gold SQL but pruned from schema: {missing_in_gold}",
                    )

        # ----- Step A: Intent Understanding -----
        if entry.x3 and entry.x3.is_ambiguous:
            return (
                RootCauseLabel.INTENT_MISINTERPRETED,
                "Clarified intent flagged as ambiguous.",
            )

        if entry.x1 and entry.x3:
            # Heuristic: nếu clarified_question ngắn hơn câu gốc nhiều => có thể mất context
            if len(entry.x3.clarified_question) < len(entry.x1.question) * 0.6:
                return (
                    RootCauseLabel.INTENT_MISSING_CONSTRAINT,
                    "Clarified intent appears to have dropped significant content.",
                )

        if entry.x11 and entry.x11.result_semantically_wrong:
            return (
                RootCauseLabel.EXEC_WRONG_RESULT,
                "Query executed successfully but returned incorrect results (semantic error).",
            )

        return RootCauseLabel.UNKNOWN, "Could not determine root cause from available signals."
