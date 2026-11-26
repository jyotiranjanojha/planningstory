"""Typed Pydantic contracts shared by all IFSP V2 specialist agents."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AgentContext(BaseModel):
    question: str
    session_id: str = "default"
    item_id: Optional[str] = None
    item_ids: List[str] = Field(default_factory=list)
    item_filter_label: Optional[str] = None
    location: Optional[str] = None
    resource: Optional[str] = None
    week_id: Optional[str] = None
    scenario_id: Optional[str] = None
    compare_scenario_id: Optional[str] = None
    show_detailed_analysis: bool = False


class AgentEvidence(BaseModel):
    source: str
    metric: str
    value: Any


class AgentResponse(BaseModel):
    agent: str
    workflow: str
    summary: str
    confirmed_findings: List[str] = Field(default_factory=list)
    root_causes: List[str] = Field(default_factory=list)
    hypotheses: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_sources: List[str] = Field(default_factory=list)
    confidence: str = "High"
    data_gaps: List[str] = Field(default_factory=list)
    recommended_next_checks: List[str] = Field(default_factory=list)
    duration_ms: int = 0

    def to_markdown(self, detailed: bool = False) -> str:
        """Render an executive, planner-friendly grounded markdown reply."""
        lines: List[str] = [self.summary]

        if self.confirmed_findings:
            lines.append("\n**Key Findings:**")
            for f in self.confirmed_findings:
                lines.append(f"- {f}")

        if self.root_causes:
            lines.append("\n**Confirmed Drivers / Exceptions:**")
            for rc in self.root_causes:
                lines.append(f"- {rc}")

        if detailed and self.rows:
            lines.append("\n**Top Records:**")
            for row in self.rows[:10]:
                parts = [f"{k}={v}" for k, v in row.items() if v is not None and v != ""]
                lines.append(f"- {', '.join(parts)}")

        if self.data_gaps:
            lines.append("\n**Data Gaps / Notes:**")
            for gap in self.data_gaps:
                lines.append(f"- {gap}")

        if self.recommended_next_checks:
            lines.append("\n**Recommended Next Checks:**")
            for nxt in self.recommended_next_checks[:3]:
                lines.append(f"- {nxt}")

        if self.evidence_sources:
            lines.append(f"\n*Evidence: {', '.join(self.evidence_sources)} | Confidence: {self.confidence} | {self.duration_ms}ms*")

        return "\n".join(lines)
