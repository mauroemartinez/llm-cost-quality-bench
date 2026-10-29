"""The two architectures under test, and the stage runner they share."""

from .common import StageOutcome, call_stage
from .modular import run_modular
from .single import run_single

__all__ = ["StageOutcome", "call_stage", "run_modular", "run_single"]
