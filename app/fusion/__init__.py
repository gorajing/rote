"""Fused skill system — the unified, surface-agnostic core.

A skill is a compiled program; each step is lowered to the cheapest primitive that reliably
achieves its intent (keyboard > crop-click > model), replayed by a pluggable Executor
(browser / desktop) and gated by a ground-truth Verifier. See contract.py for the spec
every component builds to.
"""
