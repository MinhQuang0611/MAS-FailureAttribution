"""
logging_core/__init__.py
Hệ thống Ma trận Log - Core Contribution
"""
from .log_models import (
    PaperRef,
    PipelineDetectionBundle,
    PipelineLogEntry,
    StepADetection,
    StepBDetection,
    StepCDetection,
    StepDDetection,
)
from .pipeline_spec import PIPELINE_LOGGING_SPEC, default_paper_refs_for_step
from .state_tracker import StateTracker

__all__ = [
    "PaperRef",
    "PipelineDetectionBundle",
    "PipelineLogEntry",
    "PIPELINE_LOGGING_SPEC",
    "StateTracker",
    "StepADetection",
    "StepBDetection",
    "StepCDetection",
    "StepDDetection",
    "default_paper_refs_for_step",
]
