"""
logging_core/state_tracker.py

Singleton StateTracker — ghi nhận trạng thái pipeline tại mỗi node.
Thread-safe, hỗ trợ ghi log JSON ra output_logs/raw_logs/.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .log_models import (
    PipelineDetectionBundle,
    PipelineLogEntry,
    RootCauseLabel,
    StepADetection,
    StepBDetection,
    StepCDetection,
    StepDDetection,
)


OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_logs" / "raw_logs"


class StateTracker:
    """
    Singleton theo entry_id — mỗi câu hỏi có một StateTracker riêng.
    Dùng StateTracker.get(entry_id) để lấy instance cho một câu hỏi cụ thể.
    """

    _instances: dict[str, "StateTracker"] = {}
    _lock: threading.Lock = threading.Lock()

    def __init__(self, entry_id: str, model_name: str, gold_sql: Optional[str] = None):
        self.entry_id = entry_id
        self.log = PipelineLogEntry(
            entry_id=entry_id,
            model_name=model_name,
            gold_sql=gold_sql,
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def get(cls, entry_id: str, model_name: str = "unknown",
            gold_sql: Optional[str] = None) -> "StateTracker":
        """Trả về instance đã tồn tại hoặc tạo mới."""
        with cls._lock:
            if entry_id not in cls._instances:
                cls._instances[entry_id] = cls(entry_id, model_name, gold_sql)
            return cls._instances[entry_id]

    @classmethod
    def new(cls, model_name: str, gold_sql: Optional[str] = None) -> "StateTracker":
        """Tạo entry_id mới tự động."""
        entry_id = str(uuid.uuid4())
        tracker = cls(entry_id, model_name, gold_sql)
        with cls._lock:
            cls._instances[entry_id] = tracker
        return tracker

    # ------------------------------------------------------------------
    # Step A: Intent Understanding
    # ------------------------------------------------------------------

    def set_x1(self, **kwargs):
        from .log_models import X1_RawUserQuery
        self.log.x1 = X1_RawUserQuery(**kwargs)
        return self

    def set_x2(self, **kwargs):
        from .log_models import X2_ExternalKnowledge
        self.log.x2 = X2_ExternalKnowledge(**kwargs)
        return self

    def set_x3(self, **kwargs):
        from .log_models import X3_ClarifiedIntent
        self.log.x3 = X3_ClarifiedIntent(**kwargs)
        return self

    # ------------------------------------------------------------------
    # Step B: Schema Linking
    # ------------------------------------------------------------------

    def set_x4(self, **kwargs):
        from .log_models import X4_FullSchema
        self.log.x4 = X4_FullSchema(**kwargs)
        return self

    def set_x5(self, **kwargs):
        from .log_models import X5_FilteredSchema
        self.log.x5 = X5_FilteredSchema(**kwargs)
        return self

    def set_x6(self, **kwargs):
        from .log_models import X6_SchemaAlignment
        self.log.x6 = X6_SchemaAlignment(**kwargs)
        return self

    # ------------------------------------------------------------------
    # Step C: SQL Generation
    # ------------------------------------------------------------------

    def set_x7(self, **kwargs):
        from .log_models import X7_QueryPlan
        self.log.x7 = X7_QueryPlan(**kwargs)
        return self

    def set_x8(self, **kwargs):
        from .log_models import X8_SQLSkeleton
        self.log.x8 = X8_SQLSkeleton(**kwargs)
        return self

    def set_x9(self, **kwargs):
        from .log_models import X9_CandidateSQLs
        self.log.x9 = X9_CandidateSQLs(**kwargs)
        return self

    # ------------------------------------------------------------------
    # Step D: Execution & Verification
    # ------------------------------------------------------------------

    def set_x10(self, **kwargs):
        from .log_models import X10_RuntimeError
        self.log.x10 = X10_RuntimeError(**kwargs)
        return self

    def set_x11(self, **kwargs):
        from .log_models import X11_ExecutionResult
        self.log.x11 = X11_ExecutionResult(**kwargs)
        return self

    def set_x12(self, **kwargs):
        from .log_models import X12_FailureAnalysis
        self.log.x12 = X12_FailureAnalysis(**kwargs)
        return self

    def set_x13(self, **kwargs):
        from .log_models import X13_RepairedSQL
        self.log.x13 = X13_RepairedSQL(**kwargs)
        return self

    # ------------------------------------------------------------------
    # Detection layers (Step A–D) — điền sau so khớp xᵢ hoặc gán nhãn
    # ------------------------------------------------------------------

    def _ensure_detection(self) -> PipelineDetectionBundle:
        if self.log.detection is None:
            self.log.detection = PipelineDetectionBundle()
        return self.log.detection

    def set_detection_a(self, **kwargs):
        bundle = self._ensure_detection()
        bundle.step_a = StepADetection(**kwargs)
        return self

    def set_detection_b(self, **kwargs):
        bundle = self._ensure_detection()
        bundle.step_b = StepBDetection(**kwargs)
        return self

    def set_detection_c(self, **kwargs):
        bundle = self._ensure_detection()
        bundle.step_c = StepCDetection(**kwargs)
        return self

    def set_detection_d(self, **kwargs):
        bundle = self._ensure_detection()
        bundle.step_d = StepDDetection(**kwargs)
        return self

    def set_detection_bundle(self, bundle: PipelineDetectionBundle):
        self.log.detection = bundle
        return self

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def set_evaluation(
        self,
        final_sql: str,
        execution_match: Optional[bool] = None,
        exact_match: Optional[bool] = None,
        annotated_root_cause: Optional[RootCauseLabel] = None,
    ):
        self.log.final_sql = final_sql
        self.log.execution_match = execution_match
        self.log.exact_match = exact_match
        self.log.annotated_root_cause = annotated_root_cause
        return self

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, output_dir: Optional[Path] = None) -> Path:
        """Ghi log ra file JSON."""
        target_dir = output_dir or OUTPUT_DIR
        target_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{self.entry_id[:8]}.json"
        filepath = target_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(
                self.log.model_dump(mode="json", exclude_none=True),
                f,
                ensure_ascii=False,
                indent=2,
            )
        return filepath

    def to_dict(self) -> dict:
        return self.log.model_dump(mode="json", exclude_none=True)
