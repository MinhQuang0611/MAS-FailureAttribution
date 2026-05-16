"""
Luồng chạy chính T2SQL — 4 bước A→D, bắt buộc ghi log x₁…x₁₃ và bundle detection.

Tích hợp LLM: thay lớp con hoặc inject `sql_generator` sau; mặc định dùng gold SQL
để kiểm thử end-to-end (pipeline + Evaluator + RootCauseClassifier).
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

import requests

from evaluation.evaluator import Evaluator
from evaluation.root_cause import RootCauseClassifier
from logging_core.log_models import (
    ColumnInfo,
    RootCauseLabel,
    TableInfo,
    TokenAlignment,
)
from logging_core.state_tracker import StateTracker
from utils.db_connector import DBConnector


# --- SQL generation hook (thay bằng agent LLM sau) --------------------------------


class SQLGenerator(Protocol):
    """Sinh (x₇,x₈,x₉) — triển khai thật gọi LLM + parser."""

    def generate(
        self,
        *,
        question: str,
        db_id: str,
        gold_sql: Optional[str],
        full_schema: dict[str, list[dict[str, str]]],
        db_path: Optional[str] = None,
    ) -> tuple[list[str], list[str], str, str, bool, bool, bool, bool, str]:
        """
        Returns:
            plan_steps, expected_clauses, skeleton, reasoning_trace,
            uses_agg, uses_join, uses_subq, uses_having,
            generated_sql   ← SQL thực sự được sinh (rỗng nếu fail)
        """


@dataclass
class GoldSQLGenerator:
    """Baseline: một candidate = gold SQL (để validate luồng log)."""

    def generate(
        self,
        *,
        question: str,
        db_id: str,
        gold_sql: Optional[str],
        full_schema: dict[str, list[dict[str, str]]],
        db_path: Optional[str] = None,
    ) -> tuple[list[str], list[str], str, str, bool, bool, bool, bool, str]:
        sql = (gold_sql or "").strip()
        skel, clauses = sql_to_skeleton_and_clauses(sql)
        plan = infer_plan_steps(sql)
        uj = bool(re.search(r"\bJOIN\b", sql, re.I))
        ua = bool(re.search(r"\b(COUNT|SUM|AVG|MIN|MAX|GROUP\s+BY)\b", sql, re.I))
        us = bool(re.search(r"\(\s*SELECT\b", sql, re.I))
        uh = bool(re.search(r"\bHAVING\b", sql, re.I))
        trace = f"[stub] Plan derived from reference SQL structure for db_id={db_id}."
        return plan, clauses, skel, trace, ua, uj, us, uh, sql  # gold SQL → generated_sql


@dataclass
class NLSQLGenerator:
    """Gọi API hệ thống nlsql (FastAPI) để sinh SQL."""
    api_url: str = "http://localhost:8388/api/v1/chat"

    def generate(
        self,
        *,
        question: str,
        db_id: str,
        gold_sql: Optional[str],
        full_schema: dict[str, list[dict[str, str]]],
        db_path: Optional[str] = None,
    ) -> tuple[list[str], list[str], str, str, bool, bool, bool, bool, str]:
        payload = {
            "query": question,
            "session_id": "eval_" + db_id,
            "db_path": db_path,
            "history": [],
            "num_recommend": 0
        }
        sql = ""
        trace = "[NLSQL] Calling API"
        plan = ["Parse natural language question"]
        try:
            resp = requests.post(self.api_url, json=payload, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                sql = data.get("sql") or ""       # SQL thực sự do nlsql sinh
                ans = data.get("answer") or ""
                trace = f"[NLSQL] Answer: {ans}\nExecution ms: {data.get('execution_time_ms')}"
                if sql:
                    plan = infer_plan_steps(sql)
                else:
                    trace += "\n[NLSQL] WARNING: No SQL returned (out-of-scope or error)"
            else:
                trace = f"[NLSQL] API Error: {resp.status_code} - {resp.text}"
        except Exception as e:
            trace = f"[NLSQL] Exception: {e}"

        skel, clauses = sql_to_skeleton_and_clauses(sql)
        uj = bool(re.search(r"\bJOIN\b", sql, re.I))
        ua = bool(re.search(r"\b(COUNT|SUM|AVG|MIN|MAX|GROUP\s+BY)\b", sql, re.I))
        us = bool(re.search(r"\(\s*SELECT\b", sql, re.I))
        uh = bool(re.search(r"\bHAVING\b", sql, re.I))

        return plan, clauses, skel, trace, ua, uj, us, uh, sql  # ← sql = generated SQL


def sql_to_skeleton_and_clauses(sql: str) -> tuple[str, list[str]]:
    """Rút skeleton đơn giản + danh sách mệnh đề kỳ vọng (uppercase keywords)."""
    if not sql.strip():
        return "", []

    s = re.sub(r"'[^']*'", "'?'", sql)
    s = re.sub(r"\b\d+\.?\d*\b", "?", s)
    s = re.sub(r"\s+", " ", s).strip()

    clauses: list[str] = []

    for kw in ("SELECT", "FROM", "WHERE", "GROUP BY", "HAVING", "ORDER BY", "LIMIT"):
        pattern_kw = re.escape(kw).replace(r"\ ", r"\s+")
        pattern = rf"\b{pattern_kw}\b"

        if re.search(pattern, sql, re.I):
            clauses.append(kw)

    if re.search(r"\bJOIN\b", sql, re.I) and "JOIN" not in clauses:
        clauses.append("JOIN")

    return s, clauses


def infer_plan_steps(sql: str) -> list[str]:
    steps: list[str] = ["Parse natural language question"]
    if re.search(r"\bJOIN\b", sql, re.I):
        steps.append("Identify tables and join keys")
    if re.search(r"\bWHERE\b", sql, re.I):
        steps.append("Apply filters (WHERE)")
    if re.search(r"\bGROUP BY\b", sql, re.I):
        steps.append("Aggregate with GROUP BY")
    if re.search(r"\bHAVING\b", sql, re.I):
        steps.append("Filter groups (HAVING)")
    if re.search(r"\bORDER BY\b", sql, re.I):
        steps.append("Sort results (ORDER BY)")
    steps.append("Project columns (SELECT)")
    return steps


def _schema_to_tables(db_id: str, schema: dict[str, list[dict[str, str]]]) -> list[TableInfo]:
    out: list[TableInfo] = []
    for table_name, cols in schema.items():
        out.append(
            TableInfo(
                table_name=table_name,
                columns=[ColumnInfo(name=c["name"], dtype=c.get("type")) for c in cols],
            )
        )
    return out


def _tables_in_sql(sql: str) -> set[str]:
    if not sql:
        return set()
    names: set[str] = set()
    for m in re.finditer(
        r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)",
        sql,
        re.I,
    ):
        names.add(m.group(1).lower())
    return names


def _filter_tables(full: list[TableInfo], keep: set[str]) -> list[TableInfo]:
    if not keep:
        return list(full)
    return [t for t in full if t.table_name.lower() in keep]


def _naive_token_alignments(question: str, tables: list[TableInfo]) -> list[TokenAlignment]:
    col_index: list[tuple[str, str, str]] = []
    for t in tables:
        for c in t.columns:
            col_index.append((t.table_name, c.name, f"{t.table_name}.{c.name}".lower()))
    tokens = re.findall(r"[A-Za-z_][\w]*", question)
    align: list[TokenAlignment] = []
    q_lower = question.lower()
    all_names = {t.table_name.lower() for t in tables}
    for tok in tokens:
        tl = tok.lower()
        mapped_t: Optional[str] = None
        mapped_c: Optional[str] = None
        conf = 0.0
        halluc = False # By default, normal question tokens are NOT hallucinated schema elements
        
        if tl in all_names:
            mapped_t = next(t.table_name for t in tables if t.table_name.lower() == tl)
            conf = 0.95
        else:
            for tn, cn, _ in col_index:
                if tl == cn.lower() or tl in cn.lower() or cn.lower() in tl:
                    mapped_t, mapped_c = tn, cn
                    conf = 0.7
                    break
        
        if tl in ("the", "how", "what", "which", "list", "show", "all", "and", "or"):
            conf = 0.1
            mapped_t, mapped_c = None, None
            
        align.append(
            TokenAlignment(
                token=tok,
                mapped_table=mapped_t,
                mapped_column=mapped_c,
                confidence=conf,
                is_hallucinated=halluc,
            )
        )
    return align


def _keyword_coverage(raw: str, clarified: str) -> float:
    rw = set(re.findall(r"[A-Za-z_][\w]*", raw.lower()))
    cw = set(re.findall(r"[A-Za-z_][\w]*", clarified.lower()))
    if not rw:
        return 1.0
    return round(len(rw & cw) / len(rw), 3)


@dataclass
class PipelineConfig:
    """Cấu hình một lượt chạy pipeline."""

    model_name: str
    db_dir: Path
    use_gold_as_predicted: bool = True
    apply_detection_heuristics: bool = True
    max_result_rows_logged: int = 50


class T2SQLPipeline:
    """
    Orchestrator: A → B → C → D, mỗi bước ghi đủ tín hiệu và (tùy chọn) detection.

    Ma trận thiết kế (bắt buộc log):
      A: x₁,x₂,x₃   B: x₄,x₅,x₆   C: x₇,x₈,x₉   D: x₁₀,x₁₁,x₁₂,x₁₃
    """

    def __init__(
        self,
        config: PipelineConfig,
        sql_generator: Optional[SQLGenerator] = None,
        evaluator: Optional[Evaluator] = None,
        classifier: Optional[RootCauseClassifier] = None,
    ):
        self.config = config
        self.sql_generator = sql_generator or GoldSQLGenerator()
        self.evaluator = evaluator or Evaluator(config.db_dir)
        self.classifier = classifier or RootCauseClassifier()
        self._connector = DBConnector(config.db_dir)

    def run(self, sample: dict) -> StateTracker:
        gold = (sample.get("query") or sample.get("gold_sql") or "").strip()
        db_id = sample["db_id"]
        question = sample["question"]

        tracker = StateTracker.new(model_name=self.config.model_name, gold_sql=gold or None)

        # ----- A: Intent -----
        tracker.set_x1(
            question=question,
            db_id=db_id,
            difficulty=sample.get("difficulty"),
            source=sample.get("source"),
        )
        extra_constraints: list[str] = []
        if sample.get("target_error_type"):
            extra_constraints.append(f"target_error_type={sample['target_error_type']}")
        tracker.set_x2(
            constraints=list(extra_constraints),
            domain_hints=[],
            raw_context=None,
        )
        clarified = question.strip()
        tracker.set_x3(
            clarified_question=clarified,
            subtasks=["Answer the question with a single SQL query"],
            detected_conditions=[],
            is_ambiguous=False,
            clarification_turns=0,
        )

        # ----- B: Schema -----
        try:
            schema_dict = self._connector.get_schema(db_id)
        except FileNotFoundError:
            schema_dict = {}
        tables_full = _schema_to_tables(db_id, schema_dict)
        tracker.set_x4(db_id=db_id, tables=tables_full)

        gold_tables = _tables_in_sql(gold)
        filtered = _filter_tables(tables_full, gold_tables or {t.table_name for t in tables_full})
        tracker.set_x5(
            db_id=db_id,
            selected_tables=filtered,
            pruning_rationale="Heuristic: keep tables referenced in gold SQL (stub linker); "
            "replace with LLM schema agent later.",
        )
        tracker.set_x6(alignments=_naive_token_alignments(question, filtered))

        # ----- C: SQL generation (hook) -----
        # Tính db_path để truyền cho NLSQLGenerator (nlsql chạy trong Docker container)
        # NLSQL_DB_BASE_DIR: path prefix TRONG CONTAINER (e.g. /data/databases)
        # Nếu không set → dùng local path (khi nlsql chạy ngoài Docker)
        import os as _os
        db_path_for_gen: Optional[str] = None
        # Spider dùng .sqlite, một số dataset dùng .db — thử cả hai
        for _ext in (".sqlite", ".db"):
            _candidate = self.config.db_dir / db_id / f"{db_id}{_ext}"
            if _candidate.exists():
                container_base = _os.environ.get("NLSQL_DB_BASE_DIR", "").strip()
                if container_base:
                    db_path_for_gen = f"{container_base.rstrip('/')}/{db_id}/{db_id}{_ext}"
                else:
                    db_path_for_gen = str(_candidate)
                break

        plan, clauses, skeleton, trace, ua, uj, us, uh, generated_sql = self.sql_generator.generate(
            question=question,
            db_id=db_id,
            gold_sql=gold if self.config.use_gold_as_predicted else None,
            full_schema=schema_dict,
            db_path=db_path_for_gen,
        )

        # Xác định SQL được dùng cho evaluation:
        # - GoldSQLGenerator (use_gold_as_predicted=True): dùng gold SQL
        # - NLSQLGenerator   (use_gold_as_predicted=False): dùng SQL do nlsql sinh ra
        if self.config.use_gold_as_predicted:
            candidates = [gold]
        else:
            # Dùng SQL thực sự do generator trả về (có thể rỗng nếu nlsql fail)
            candidates = [generated_sql] if generated_sql.strip() else [""]

        selected_sql = candidates[0] if candidates else ""

        tracker.set_x7(
            steps=plan,
            reasoning_trace_raw=trace,
            uses_aggregation=ua,
            uses_join=uj,
            uses_subquery=us,
            uses_having=uh,
        )
        tracker.set_x8(skeleton=skeleton, expected_clauses=clauses)
        tracker.set_x9(
            candidates=candidates,
            selected_index=0 if candidates else None,
            selected_sql=selected_sql or None,
            diversity_metrics="n_candidates=1 (stub); increase for self-consistency studies.",
        )

        # ----- D: Execution -----
        self._step_d_execute_and_reflect(tracker, db_id, gold, selected_sql)

        if self.config.apply_detection_heuristics:
            self._fill_detection_bundle(tracker, gold)

        return tracker

    def _step_d_execute_and_reflect(
        self,
        tracker: StateTracker,
        db_id: str,
        gold_sql: str,
        predicted_sql: str,
    ) -> None:
        db_path = self.evaluator._get_db_path(db_id)
        if not predicted_sql.strip():
            tracker.set_x10(has_error=True, error_type="EmptySQL", error_message="No SQL to execute")
            tracker.set_x11(rows=[], row_count=0, is_empty=True)
            tracker.set_evaluation(final_sql="", execution_match=False, exact_match=False)
            label, expl = self.classifier.classify(tracker.log)
            tracker.set_x12(
                predicted_root_cause=label,
                explanation=expl,
                reflection_trace=f"[rule-based] {expl}",
                taxonomy_label=str(label),
            )
            tracker.set_x13(repaired_sql=None, repair_applied=False, introduced_new_error=False)
            tracker.set_evaluation(
                final_sql="",
                execution_match=False,
                exact_match=False,
                annotated_root_cause=label,
            )
            return

        t0 = time.perf_counter()
        execution_match: Optional[bool] = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.execute(predicted_sql)
            raw_rows = cur.fetchall()
            conn.close()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            rows: list[Any] = [tuple(r) for r in raw_rows[: self.config.max_result_rows_logged]]
            n = len(raw_rows)
            is_empty = n == 0
            execution_match, _ = self.evaluator.execution_match(db_id, predicted_sql, gold_sql)
            tracker.set_x10(has_error=False, error_type=None, error_message=None)
            sem_wrong: Optional[bool]
            if execution_match:
                sem_wrong = False
            elif is_empty:
                sem_wrong = True
            else:
                sem_wrong = True
            tracker.set_x11(
                rows=rows,
                row_count=n,
                is_empty=is_empty,
                execution_time_ms=round(elapsed_ms, 2),
                result_semantically_wrong=sem_wrong,
            )
        except Exception as e:
            execution_match = False
            tracker.set_x10(
                has_error=True,
                error_type=type(e).__name__,
                error_message=str(e),
                error_taxonomy_tag="runtime_sqlite",
            )
            tracker.set_x11(rows=[], row_count=0, is_empty=True)

        tracker.set_evaluation(
            final_sql=predicted_sql,
            execution_match=execution_match,
            exact_match=self.evaluator.exact_set_match(predicted_sql, gold_sql) if gold_sql else None,
        )

        label, expl = self.classifier.classify(tracker.log)
        tracker.set_x12(
            predicted_root_cause=label,
            explanation=expl,
            suggested_fix=None,
            reflection_trace=f"[rule-based] {expl}",
            taxonomy_label=str(label),
        )
        tracker.set_x13(repaired_sql=None, repair_applied=False, introduced_new_error=False)
        tracker.set_evaluation(
            final_sql=predicted_sql,
            execution_match=execution_match,
            exact_match=self.evaluator.exact_set_match(predicted_sql, gold_sql) if gold_sql else None,
            annotated_root_cause=label,
        )

    def _fill_detection_bundle(self, tracker: StateTracker, gold_sql: str) -> None:
        log = tracker.log
        assert log.x1 and log.x3

        cov = _keyword_coverage(log.x1.question, log.x3.clarified_question)
        sem_ok = cov >= 0.85 or log.x3.clarified_question.strip() == log.x1.question.strip()
        rw = set(re.findall(r"[A-Za-z_][\w]*", log.x1.question.lower()))
        cw = set(re.findall(r"[A-Za-z_][\w]*", log.x3.clarified_question.lower()))
        over_inf = bool(cw - rw) and len(cw - rw) >= 3
        missing_constr = len(log.x3.clarified_question.strip()) < 0.5 * max(len(log.x1.question.strip()), 1)

        tracker.set_detection_a(
            missing_constraints=missing_constr,
            misinterpreted_intent=False,
            over_inference_or_hallucinated_assumptions=over_inf,
            keyword_coverage_score=cov,
            semantic_consistency_ok=sem_ok,
            notes=["AmbiSQL / SQL-of-Thought: heuristic keyword overlap x₁ vs x₃"],
        )

        miss_tbl = False
        hall = False
        if log.x4 and log.x5 and gold_sql:
            full_ids = {t.table_name for t in log.x4.tables}
            sel_ids = {t.table_name for t in log.x5.selected_tables}
            for tname in _tables_in_sql(gold_sql):
                if tname in full_ids and tname not in sel_ids:
                    miss_tbl = True
        if log.x6:
            hall = any(a.is_hallucinated for a in log.x6.alignments)
        amb = any(0.3 < a.confidence < 0.55 for a in (log.x6.alignments if log.x6 else []))
        tracker.set_detection_b(
            missing_tables_or_columns=miss_tbl,
            hallucinated_schema_elements=hall,
            wrong_column_mapping=False,
            ambiguous_token_alignment=amb,
            notes=["SCoT2S / EMLC / schema linking: table coverage vs gold; token alignment confidence"],
        )

        plan_ok = True
        plan_issues: list[str] = []
        if log.x7 and log.x9 and log.x7.uses_join and log.x9.selected_sql:
            if "JOIN" not in (log.x9.selected_sql).upper():
                plan_ok = False
                plan_issues.append("Plan expects JOIN but SQL has no JOIN")
        sk_ok = True
        missing_cl: list[str] = []
        if log.x8 and log.x9 and log.x9.selected_sql:
            u = log.x9.selected_sql.upper()
            for c in log.x8.expected_clauses:
                if c == "JOIN":
                    if "JOIN" not in u:
                        missing_cl.append(c)
                elif c.replace(" ", "") not in u.replace(" ", ""):
                    missing_cl.append(c)
            sk_ok = not missing_cl
        n_cand = len(log.x9.candidates) if log.x9 else 0
        self_cons = 1.0 if n_cand <= 1 else 0.0
        tracker.set_detection_c(
            plan_logical_valid=plan_ok,
            plan_issues=plan_issues,
            skeleton_structure_ok=sk_ok,
            skeleton_missing_clauses=missing_cl,
            candidates_self_consistency_score=self_cons,
            candidates_diversity_vs_correctness_notes="Single candidate (stub); diversity N/A",
            notes=["SCoT2S / EMLC skeleton / SEA-SQL: clause check vs x₈"],
        )

        x10 = log.x10
        x11 = log.x11
        syn = bool(x10 and x10.has_error)
        sem = bool(x11 and (x11.is_empty or x11.result_semantically_wrong))
        x12 = log.x12
        diag_ok = x12 is not None and (
            log.execution_match is True or x12.predicted_root_cause != RootCauseLabel.UNKNOWN
        )
        x13 = log.x13
        x13_ok: Optional[bool] = None
        if x13 and x13.repair_applied and x13.repaired_sql and log.x9 and log.x9.selected_sql:
            x13_ok = x13.repaired_sql.strip() != log.x9.selected_sql.strip() and not x13.introduced_new_error

        tracker.set_detection_d(
            syntax_error_detected=syn,
            semantic_error_empty_or_wrong_result=sem and not syn,
            x12_diagnosis_matches_error_taxonomy=diag_ok,
            x13_correction_verified_no_new_errors=x13_ok,
            notes=["EMLC execution / SQLFixAgent: combine x₉+x₁₀+x₁₁; x₁₃ stub"],
        )


def run_pipeline_for_sample(
    sample: dict,
    model_name: str,
    db_dir: Path,
    *,
    use_gold_as_predicted: bool = True,
    use_nlsql_api: bool = False,
) -> StateTracker:
    """API tương thích `main.py` — chạy orchestrator một sample."""
    cfg = PipelineConfig(
        model_name=model_name,
        db_dir=db_dir,
        # Khi dùng NLSQLGenerator: Tắt chế độ gold-as-predicted
        # để evaluation dùng SQL thực sự do nlsql sinh ra.
        use_gold_as_predicted=use_gold_as_predicted and not use_nlsql_api,
        apply_detection_heuristics=True,
    )
    generator = NLSQLGenerator() if use_nlsql_api else None
    return T2SQLPipeline(cfg, sql_generator=generator).run(sample)
