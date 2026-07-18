"""Faithful structured explanations derived from engine output."""

from explain.builder import explain_allocation, explain_recommendation
from explain.frontier import explain_frontier, explain_what_if

__all__ = [
	"explain_allocation",
	"explain_frontier",
	"explain_recommendation",
	"explain_what_if",
]
