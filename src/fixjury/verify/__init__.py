"""Verification: policy rules, the container runner and verdict assembly."""

from __future__ import annotations

from fixjury.verify.policy import (
    ChangedFile,
    PolicyResult,
    Violation,
    check_policy,
    parse_changed_files,
)
from fixjury.verify.runner import VerificationError, apply_patch, overlay_heldout, verify_workspace
from fixjury.verify.service import VerifyError, store_verdict, verify_run
from fixjury.verify.verdict import CheckOutcome, VerificationResult, assemble

__all__ = [
    "ChangedFile",
    "CheckOutcome",
    "PolicyResult",
    "VerificationError",
    "VerificationResult",
    "VerifyError",
    "Violation",
    "apply_patch",
    "assemble",
    "check_policy",
    "overlay_heldout",
    "parse_changed_files",
    "store_verdict",
    "verify_run",
    "verify_workspace",
]
