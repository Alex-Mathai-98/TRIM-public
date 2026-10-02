# Benchmark Utilities

> **Purpose:** Benchmark-specific helpers that sit outside the generic minimization core (e.g. kernel/Syzbot bug handling). Keeps benchmark concerns isolated so `core/` and `strategies/` stay benchmark-agnostic.

---

## Contents

| Item | Purpose |
|---|---|
| `__init__.py` | Package docstring only. |
| `kernel/` | Kernel-benchmark types & helpers: `BugData`/`CrashData`, `LLMResponseHistory` decision tree, kGym job-result types, Anthropic-format conversation history, and batch linux-repo cleaning. See `kernel/AGENTS.md`. |

## Note

`BugData` (from `kernel/bug_data.py`) is re-exported by `core/__init__.py` for backward compatibility — several of these modules previously lived under `core/` and were moved here.
