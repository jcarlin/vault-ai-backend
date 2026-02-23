"""Backward-compatibility re-export — the real implementation is in training/service.py."""

from app.services.training.service import TrainingService, _row_to_response, VALID_TRANSITIONS

__all__ = ["TrainingService", "_row_to_response", "VALID_TRANSITIONS"]
