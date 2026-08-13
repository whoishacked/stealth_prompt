"""Stealth Prompt: black-box prompt-injection testing for authorized targets.

This package is the destination for the incremental migration described in
``internal-docs/migration-plan.md``. During milestone 1 it contains only the console
entry-point foundation; the runner, adapters, strategies, and oracles arrive in
later milestones. The legacy Selenium implementation remains under ``src/`` and
is reached through ``python main.py``.
"""

from __future__ import annotations

__version__ = "0.2.0"

__all__ = ["__version__"]
