# Self-Learning Strategy for IFSP Agent Suite

## Overview

As BY ESP datasets evolve, your agents must adapt without manual retraining. This strategy implements continuous learning across five dimensions:

1. **Data-level learning** — Detect schema changes and data drift
2. **Query-level learning** — Learn from user feedback and successful patterns
3. **Evidence-level learning** — Calibrate confidence and grounding quality
4. **LLM-level learning** — Optimize prompts and examples
5. **Validation-level learning** — Refine quality gates based on outcomes

---

## 1. Data-Level Learning

### Problem
- BY snapshot schemas evolve (new fields, dropped columns, renamed keys)
- Data distributions shift (new planning scenarios, different volumes)
- New entity types emerge (products, locations, resources)

### Solution: Data Versioning and Drift Detection

#### 1.1 Data Fingerprinting

Track metadata for each snapshot load:

```python
# webapp/app/services/data_profiler.py

from dataclasses import dataclass
import hashlib
import pandas as pd
from datetime import datetime

@dataclass
class DataFingerprint:
    """Record dataset characteristics for drift detection."""
    timestamp: str
    file_name: str
    schema_hash: str  # Hash of column names and dtypes
    row_count: int
    key_coverage: dict  # % coverage of critical keys
    value_ranges: dict  # Min/max for numeric fields
    null_patterns: dict  # Null counts per column
    new_columns: list
    dropped_columns: list
    
    def __hash__(self):
        return hashlib.md5(
            f"{self.schema_hash}{self.row_count}".encode()
        ).hexdigest()

class DataDriftDetector:
    """Monitor data changes and trigger schema updates."""
    
    def __init__(self, baseline_fingerprint_path: str):
        self.baseline = self._load_fingerprint(baseline_fingerprint_path)
        self.fingerprints = []
    
    def profile_snapshot(self, df: pd.DataFrame, filename: str) -> DataFingerprint:
        """Create fingerprint for new snapshot."""
        columns = sorted(df.columns)
        dtypes = {col: str(df[col].dtype) for col in columns}
        schema_hash = hashlib.md5(
            str(dtypes).encode()
        ).hexdigest()
        
        numeric_cols = df.select_dtypes(include=['number']).columns
        value_ranges = {
            col: {"min": df[col].min(), "max": df[col].max()}
            for col in numeric_cols
        }
        
        fingerprint = DataFingerprint(
            timestamp=datetime.utcnow().isoformat(),
            file_name=filename,
            schema_hash=schema_hash,
            row_count=len(df),
            key_coverage={
                col: (1 - df[col].isna().sum() / len(df)) 
                for col in self._critical_keys(filename)
            },
            value_ranges=value_ranges,
            null_patterns={
                col: int(df[col].isna().sum())
                for col in columns
            },
            new_columns=self._find_new_columns(columns),
            dropped_columns=self._find_dropped_columns(columns),
        )
        
        self.fingerprints.append(fingerprint)
        return fingerprint
    
    def detect_drift(self, fingerprint: DataFingerprint) -> dict:
        """Return drift signals and severity."""
        if not self.baseline:
            return {"status": "baseline", "reason": "No baseline yet"}
        
        drift_signals = {}
        
        # Schema drift
        if fingerprint.schema_hash != self.baseline.schema_hash:
            drift_signals["schema_changed"] = {
                "new": fingerprint.new_columns,
                "dropped": fingerprint.dropped_columns,
                "severity": "high" if len(fingerprint.dropped_columns) > 0 else "medium"
            }
        
        # Volume drift
        volume_change = abs(
            (fingerprint.row_count - self.baseline.row_count) / 
            max(self.baseline.row_count, 1)
        )
        if volume_change > 0.2:  # 20% threshold
            drift_signals["volume_drift"] = {
                "change_pct": volume_change * 100,
                "severity": "medium"
            }
        
        # Distribution drift
        for col, baseline_range in self.baseline.value_ranges.items():
            if col in fingerprint.value_ranges:
                curr_range = fingerprint.value_ranges[col]
                range_shift = (
                    abs(curr_range["min"] - baseline_range["min"]) +
                    abs(curr_range["max"] - baseline_range["max"])
                ) / max(abs(baseline_range["max"] - baseline_range["min"]), 1)
                
                if range_shift > 0.3:  # 30% threshold
                    drift_signals[f"distribution_drift_{col}"] = {
                        "baseline_range": baseline_range,
                        "current_range": curr_range,
                        "severity": "medium"
                    }
        
        # Key coverage drift
        for key, baseline_coverage in self.baseline.key_coverage.items():
            if key in fingerprint.key_coverage:
                curr_coverage = fingerprint.key_coverage[key]
                if abs(curr_coverage - baseline_coverage) > 0.05:  # 5% threshold
                    drift_signals[f"key_coverage_drift_{key}"] = {
                        "baseline_coverage": baseline_coverage,
                        "current_coverage": curr_coverage,
                        "severity": "high" if curr_coverage < 0.9 else "medium"
                    }
        
        return {
            "has_drift": len(drift_signals) > 0,
            "signals": drift_signals,
            "timestamp": fingerprint.timestamp,
            "action_required": any(
                s.get("severity") == "high" 
                for s in drift_signals.values()
            )
        }
    
    def _critical_keys(self, filename: str) -> list:
        """Return critical key fields for each dataset type."""
        critical_keys_map = {
            "if_snop_items": ["ITEM_ID"],
            "if_snop_sku": ["SKU_ID"],
            "if_snop_locations": ["LOCATION_ID"],
            "if_snop_billofmaterials": ["PARENT_ITEM_ID", "COMPONENT_ITEM_ID"],
            "if_snop_customerorder": ["ORDER_ID", "ORDER_LINE_ID"],
            "by_if_snop_out_planorder": ["PLAN_ORDER_ID", "ITEM_ID"],
        }
        
        for pattern, keys in critical_keys_map.items():
            if pattern in filename:
                return keys
        return []
    
    def _find_new_columns(self, current_cols: list) -> list:
        if not self.baseline:
            return []
        baseline_cols = set(self.baseline.schema_hash.split())
        return list(set(current_cols) - baseline_cols)
    
    def _find_dropped_columns(self, current_cols: list) -> list:
        if not self.baseline:
            return []
        baseline_cols = set(self.baseline.schema_hash.split())
        return list(baseline_cols - set(current_cols))
```

#### 1.2 Automated Schema Registry Update

When drift is detected:

```python
# webapp/app/services/schema_registry.py

class SchemaRegistry:
    """Central registry of known schemas with versioning."""
    
    def __init__(self, registry_path: str):
        self.registry_path = registry_path
        self.schemas = self._load_schemas()
    
    def update_schema(self, table_name: str, new_schema: dict, drift_report: dict):
        """Record schema change with drift context."""
        version = len(self.schemas.get(table_name, [])) + 1
        
        schema_record = {
            "version": version,
            "timestamp": datetime.utcnow().isoformat(),
            "schema": new_schema,
            "drift_signals": drift_report,
            "status": "auto_detected"
        }
        
        if table_name not in self.schemas:
            self.schemas[table_name] = []
        
        self.schemas[table_name].append(schema_record)
        self._persist_schemas()
        
        # Alert validation agent to review new schema
        return self._notify_validation_agent(table_name, schema_record)
    
    def get_active_schema(self, table_name: str) -> dict:
        """Retrieve latest known schema."""
        if table_name in self.schemas:
            return self.schemas[table_name][-1]["schema"]
        return {}
    
    def get_field_lineage(self, table_name: str, field_name: str) -> dict:
        """Track field evolution across schema versions."""
        lineage = []
        
        if table_name in self.schemas:
            for record in self.schemas[table_name]:
                if field_name in record["schema"]:
                    lineage.append({
                        "version": record["version"],
                        "dtype": record["schema"][field_name],
                        "timestamp": record["timestamp"]
                    })
        
        return {"field": field_name, "lineage": lineage}
```

---

## 2. Query-Level Learning

### Problem
- Successful queries reveal common planning patterns
- User corrections indicate LLM misunderstandings
- Rare query types may break existing parsers

### Solution: Query Analytics and Pattern Library

#### 2.1 Query Logging and Success Metrics

```python
# webapp/app/services/query_analytics.py

from enum import Enum
import json
from datetime import datetime

class QueryStatus(Enum):
    SUCCESSFUL = "successful"
    CORRECTED_BY_USER = "corrected_by_user"
    FAILED_VALIDATION = "failed_validation"
    HALLUCINATION_DETECTED = "hallucination_detected"
    TIMEOUT = "timeout"

class QueryAnalytics:
    """Track query patterns and success rates."""
    
    def __init__(self, log_path: str):
        self.log_path = log_path
    
    def log_query(self, query_data: dict):
        """Record query execution with outcome."""
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "user_id": query_data.get("user_id"),
            "raw_question": query_data.get("raw_question"),
            "intent": query_data.get("detected_intent"),
            "entities": query_data.get("entities"),
            "agent_used": query_data.get("agent_name"),
            "response_time_ms": query_data.get("duration_ms"),
            "status": query_data.get("status"),
            "feedback": query_data.get("user_feedback"),  # User rate/correct/reject
            "evidence_sources": query_data.get("evidence_sources"),
            "confidence_score": query_data.get("confidence"),
        }
        
        with open(self.log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    
    def analyze_patterns(self, time_window_days: int = 7) -> dict:
        """Extract successful query patterns."""
        records = self._load_recent_records(time_window_days)
        successful = [r for r in records if r["status"] == QueryStatus.SUCCESSFUL.value]
        
        intent_patterns = {}
        for record in successful:
            intent = record.get("intent")
            if intent:
                if intent not in intent_patterns:
                    intent_patterns[intent] = {
                        "count": 0,
                        "avg_confidence": 0,
                        "typical_entities": {},
                        "example_questions": []
                    }
                
                pattern = intent_patterns[intent]
                pattern["count"] += 1
                pattern["avg_confidence"] = (
                    (pattern["avg_confidence"] * (pattern["count"] - 1) + 
                     record["confidence_score"]) / pattern["count"]
                )
                
                # Track typical entities
                for entity_type, entity_value in record.get("entities", {}).items():
                    if entity_type not in pattern["typical_entities"]:
                        pattern["typical_entities"][entity_type] = []
                    pattern["typical_entities"][entity_type].append(entity_value)
                
                if len(pattern["example_questions"]) < 3:
                    pattern["example_questions"].append(record["raw_question"])
        
        return {
            "period_days": time_window_days,
            "total_successful": len(successful),
            "intent_patterns": intent_patterns
        }
    
    def get_correction_insights(self) -> dict:
        """Identify where users correct agent responses."""
        records = self._load_recent_records(30)
        corrected = [r for r in records if r["status"] == QueryStatus.CORRECTED_BY_USER.value]
        
        correction_patterns = {}
        for record in corrected:
            intent = record.get("intent", "unknown")
            agent = record.get("agent_used", "unknown")
            key = f"{intent}_{agent}"
            
            if key not in correction_patterns:
                correction_patterns[key] = {
                    "correction_count": 0,
                    "typical_issues": [],
                    "examples": []
                }
            
            correction_patterns[key]["correction_count"] += 1
            
            # Log what user corrected
            if "user_feedback" in record and record["user_feedback"]:
                correction_patterns[key]["typical_issues"].append(
                    record["user_feedback"]
                )
            
            if len(correction_patterns[key]["examples"]) < 2:
                correction_patterns[key]["examples"].append({
                    "question": record["raw_question"],
                    "feedback": record.get("user_feedback")
                })
        
        return correction_patterns
```

#### 2.2 Few-Shot Example Database

As successful patterns emerge, add them to agent prompts:

```python
# webapp/app/services/few_shot_learner.py

class FewShotLearner:
    """Build dynamic few-shot examples from successful queries."""
    
    def __init__(self, example_db_path: str):
        self.example_db_path = example_db_path
        self.examples = self._load_examples()
    
    def add_successful_example(self, query_record: dict):
        """Store successful query as few-shot example."""
        if query_record["status"] == QueryStatus.SUCCESSFUL.value:
            example = {
                "timestamp": datetime.utcnow().isoformat(),
                "question": query_record["raw_question"],
                "detected_intent": query_record["intent"],
                "entities": query_record["entities"],
                "agent_used": query_record["agent_name"],
                "response_snippet": query_record.get("response_text", ""),
                "confidence": query_record["confidence_score"],
                "user_satisfaction": query_record.get("user_rating", 0),
            }
            
            self.examples.append(example)
            self._persist_examples()
    
    def get_examples_for_intent(self, intent: str, limit: int = 3) -> list:
        """Retrieve top examples for given intent."""
        intent_examples = [
            ex for ex in self.examples
            if ex["detected_intent"] == intent
        ]
        
        # Sort by confidence and user satisfaction
        intent_examples.sort(
            key=lambda x: (x["confidence"], x["user_satisfaction"]),
            reverse=True
        )
        
        return intent_examples[:limit]
    
    def build_dynamic_few_shot_prompt(self, intent: str) -> str:
        """Generate few-shot prompt section for agent."""
        examples = self.get_examples_for_intent(intent, limit=3)
        
        if not examples:
            return ""
        
        prompt_text = "\n## Recent Successful Examples\n"
        
        for i, ex in enumerate(examples, 1):
            prompt_text += f"\n### Example {i}\n"
            prompt_text += f"**User Question**: {ex['question']}\n"
            prompt_text += f"**Detected Intent**: {ex['detected_intent']}\n"
            prompt_text += f"**Entities**: {json.dumps(ex['entities'], indent=2)}\n"
            prompt_text += f"**Agent Used**: {ex['agent_used']}\n"
            prompt_text += f"**Response Quality**: {ex['confidence']:.2f} confidence, "
            prompt_text += f"{ex['user_satisfaction']}/5 satisfaction\n"
        
        return prompt_text
```

---

## 3. Evidence-Level Learning

### Problem
- Grounding quality varies across query types
- Confidence scores may be miscalibrated
- Evidence sources have different reliability

### Solution: Confidence Calibration and Evidence Audit

#### 3.1 Confidence Score Audit

```python
# webapp/app/services/confidence_auditor.py

class ConfidenceAuditor:
    """Validate and calibrate confidence scores."""
    
    def __init__(self, audit_log_path: str):
        self.audit_log_path = audit_log_path
    
    def audit_confidence(self, response_record: dict) -> dict:
        """Verify if claimed confidence matches actual correctness."""
        audit = {
            "timestamp": datetime.utcnow().isoformat(),
            "claimed_confidence": response_record["confidence_score"],
            "actual_correctness": self._verify_response(response_record),
            "calibration_gap": 0,
            "recommendation": "keep"
        }
        
        # Compare claimed vs actual
        audit["calibration_gap"] = abs(
            audit["claimed_confidence"] - audit["actual_correctness"]
        )
        
        # If confidence is overestimated, flag for retraining
        if audit["calibration_gap"] > 0.15:
            audit["recommendation"] = "recalibrate"
        
        self._log_audit(audit)
        return audit
    
    def _verify_response(self, response_record: dict) -> float:
        """Check if response was actually correct."""
        # Get user feedback
        user_rating = response_record.get("user_rating", 0)  # 0-5
        user_corrections = response_record.get("user_corrections", [])
        
        # If user approved, score = 1.0
        if user_rating >= 4 and len(user_corrections) == 0:
            return 1.0
        
        # If user corrected, score lower
        if len(user_corrections) > 0:
            return 0.5
        
        # If user rejected, score = 0
        if user_rating <= 2:
            return 0.0
        
        # Default to mid-range if unknown
        return 0.7
    
    def get_calibration_report(self, agent_name: str = None) -> dict:
        """Report confidence calibration by agent."""
        audits = self._load_audits()
        
        if agent_name:
            audits = [a for a in audits if a.get("agent_name") == agent_name]
        
        avg_gap = sum(a["calibration_gap"] for a in audits) / max(len(audits), 1)
        overcalibrated = sum(
            1 for a in audits if a["claimed_confidence"] > a["actual_correctness"]
        )
        
        return {
            "agent": agent_name or "all",
            "audit_count": len(audits),
            "avg_calibration_gap": avg_gap,
            "overcalibrated_pct": (overcalibrated / max(len(audits), 1)) * 100,
            "recommendation": (
                "agent needs retraining" if avg_gap > 0.15
                else "confidence scores acceptable"
            )
        }
```

#### 3.2 Evidence Source Reliability Scoring

```python
# webapp/app/services/evidence_reliability.py

class EvidenceReliabilityTracker:
    """Track which evidence sources lead to good outcomes."""
    
    def __init__(self):
        self.source_scores = {}  # source -> (correct_count, total_count)
    
    def record_evidence_outcome(self, response_record: dict):
        """Track if evidence source led to correct outcome."""
        evidence_sources = response_record.get("evidence_sources", [])
        outcome_correct = response_record.get("user_approved", False)
        
        for source in evidence_sources:
            if source not in self.source_scores:
                self.source_scores[source] = {"correct": 0, "total": 0}
            
            self.source_scores[source]["total"] += 1
            if outcome_correct:
                self.source_scores[source]["correct"] += 1
    
    def get_source_reliability(self) -> dict:
        """Return reliability scores for each evidence source."""
        reliability = {}
        
        for source, scores in self.source_scores.items():
            if scores["total"] > 0:
                reliability[source] = {
                    "reliability_score": scores["correct"] / scores["total"],
                    "sample_size": scores["total"],
                    "status": self._rate_status(
                        scores["correct"] / scores["total"],
                        scores["total"]
                    )
                }
        
        return reliability
    
    def _rate_status(self, reliability_score: float, sample_size: int) -> str:
        """Rate source as trusted, investigate, or unreliable."""
        if sample_size < 5:
            return "insufficient_samples"
        if reliability_score >= 0.9:
            return "trusted"
        elif reliability_score >= 0.7:
            return "investigate"
        else:
            return "unreliable"
```

---

## 4. LLM-Level Learning

### Problem
- LLM prompts may not match evolving query patterns
- Hallucination patterns recur
- Domain-specific terminology changes

### Solution: Prompt Optimization and Hallucination Prevention

#### 4.1 Dynamic Prompt Optimization

```python
# webapp/app/services/prompt_optimizer.py

class PromptOptimizer:
    """Adapt agent prompts based on query outcome analytics."""
    
    def __init__(self, prompt_template_path: str):
        self.template_path = prompt_template_path
        self.base_prompt = self._load_base_prompt()
    
    def optimize_prompt_for_intent(self, intent: str, analytics: dict) -> str:
        """Generate optimized prompt for intent."""
        prompt = self.base_prompt + "\n"
        
        # Add successful examples
        few_shot = self._build_few_shot_section(intent, analytics)
        prompt += few_shot
        
        # Add guardrails for common hallucinations
        hallucination_guards = self._build_hallucination_guards(intent, analytics)
        prompt += hallucination_guards
        
        # Add domain context
        domain_context = self._build_domain_context(intent, analytics)
        prompt += domain_context
        
        return prompt
    
    def _build_hallucination_guards(self, intent: str, analytics: dict) -> str:
        """Generate prompt section to prevent hallucination."""
        guards = "\n## Hallucination Prevention\n"
        
        # Check if this intent had hallucination issues
        if f"hallucination_detected_{intent}" in analytics:
            common_hallucinations = analytics[f"hallucination_detected_{intent}"]
            
            guards += "\nDO NOT:\n"
            for hallucination in common_hallucinations[:3]:
                guards += f"- {hallucination}\n"
        
        # Add general guardrails
        guards += """
- DO NOT invent entity values that don't appear in the data
- DO NOT fabricate lineage paths
- DO NOT speculate about solver behavior without evidence
- If data is insufficient, explicitly state the gap
"""
        
        return guards
    
    def _build_domain_context(self, intent: str, analytics: dict) -> str:
        """Add domain knowledge relevant to intent."""
        context = "\n## Domain Context\n"
        
        if "root_cause" in intent.lower():
            context += """
When performing root-cause analysis:
1. Trace backwards from unmet demand
2. Check: availability, capacity, sourcing, lead time, BOM
3. Distinguish confirmed causes from hypotheses
4. Quantify impact where data supports it
"""
        
        elif "compare" in intent.lower():
            context += """
When comparing scenarios:
1. Validate comparability of keys and metrics first
2. Normalize week/scenario scope
3. Rank deltas by impact magnitude
4. Identify drivers (master data, BOM, parameter changes)
"""
        
        elif "validate" in intent.lower():
            context += """
When validating data:
1. Check key consistency across tables
2. Verify value ranges match business rules
3. Classify issues by severity (critical/high/medium/low)
4. Link defects to planning impact
"""
        
        return context
```

#### 4.2 Hallucination Detection and Correction

```python
# webapp/app/services/hallucination_detector.py

class HallucinationDetector:
    """Identify and log hallucinations in LLM responses."""
    
    def __init__(self, data_validator):
        self.validator = data_validator
    
    def check_response_factuality(self, response: dict, evidence: dict) -> dict:
        """Verify if response claims are supported by evidence."""
        claims = self._extract_claims(response["text"])
        
        hallucinations = []
        
        for claim in claims:
            is_supported = self._verify_claim(claim, evidence)
            
            if not is_supported:
                hallucinations.append({
                    "claim": claim,
                    "type": self._classify_hallucination(claim, evidence),
                    "severity": "high"  # Hallucinations are always high severity
                })
        
        return {
            "response_id": response.get("id"),
            "has_hallucinations": len(hallucinations) > 0,
            "hallucination_count": len(hallucinations),
            "hallucinations": hallucinations
        }
    
    def _verify_claim(self, claim: str, evidence: dict) -> bool:
        """Check if claim is supported by evidence."""
        # Extract key entities from claim
        entities = self._extract_entities(claim)
        
        # Check if entities appear in evidence
        for entity_type, entity_value in entities.items():
            if entity_type in evidence:
                if entity_value not in evidence[entity_type]:
                    return False
        
        return True
    
    def _classify_hallucination(self, claim: str, evidence: dict) -> str:
        """Classify hallucination type."""
        if any(entity not in evidence for entity in claim.split()):
            return "fabricated_entity"
        elif "because" in claim.lower() or "caused by" in claim.lower():
            return "unsupported_causation"
        elif any(num in claim for num in ["100", "99", "0"]):
            return "fabricated_number"
        else:
            return "unsupported_claim"
    
    def _extract_claims(self, text: str) -> list:
        """Parse response text into factual claims."""
        # Simple heuristic: sentences are claims
        claims = text.split(".")
        return [c.strip() for c in claims if c.strip()]
    
    def _extract_entities(self, claim: str) -> dict:
        """Extract entity references from claim."""
        entities = {}
        # This would use entity recognition or regex patterns
        return entities
```

---

## 5. Validation-Level Learning

### Problem
- Quality gates may be too strict or too lenient
- Business rule thresholds evolve
- New constraint types emerge

### Solution: Dynamic Validation Rule Tuning

#### 5.1 Validation Rule Effectiveness Tracking

```python
# webapp/app/services/validation_tuner.py

class ValidationTuner:
    """Learn which validation rules catch real issues."""
    
    def __init__(self, rules_path: str):
        self.rules_path = rules_path
        self.rules = self._load_rules()
        self.rule_effectiveness = {}
    
    def track_rule_outcome(self, rule_name: str, outcome: dict):
        """Record if validation rule caught a real issue."""
        if rule_name not in self.rule_effectiveness:
            self.rule_effectiveness[rule_name] = {
                "triggered": 0,
                "user_confirmed": 0,
                "false_positives": 0,
                "false_negatives": 0
            }
        
        stats = self.rule_effectiveness[rule_name]
        stats["triggered"] += 1
        
        if outcome.get("user_confirmed"):
            stats["user_confirmed"] += 1
        elif outcome.get("user_rejected"):
            stats["false_positives"] += 1
        
        # Later, if an issue occurs that rule didn't catch:
        if outcome.get("missed_by_rule"):
            stats["false_negatives"] += 1
    
    def get_rule_health(self) -> dict:
        """Report validation rule effectiveness."""
        health = {}
        
        for rule_name, stats in self.rule_effectiveness.items():
            total = stats["triggered"]
            
            if total == 0:
                continue
            
            precision = stats["user_confirmed"] / total if total > 0 else 0
            health[rule_name] = {
                "precision": precision,
                "false_positive_rate": stats["false_positives"] / total,
                "recommendation": (
                    "increase_threshold" if precision < 0.6
                    else "keep" if precision > 0.8
                    else "investigate"
                )
            }
        
        return health
    
    def auto_tune_thresholds(self, rule_name: str) -> dict:
        """Suggest threshold adjustments based on outcomes."""
        stats = self.rule_effectiveness.get(rule_name, {})
        rule = self.rules.get(rule_name, {})
        
        current_threshold = rule.get("threshold")
        false_positive_rate = stats.get("false_positives", 0) / max(stats.get("triggered", 1), 1)
        
        adjustment = None
        if false_positive_rate > 0.3:
            adjustment = current_threshold * 1.1  # Increase threshold
        elif stats.get("false_negatives", 0) > 0:
            adjustment = current_threshold * 0.9  # Lower threshold
        
        return {
            "rule_name": rule_name,
            "current_threshold": current_threshold,
            "suggested_threshold": adjustment,
            "confidence": 0.7 if stats.get("triggered", 0) > 20 else 0.4
        }
```

---

## Implementation Architecture

### Feedback Loop Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                    Planner Query                                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
         ┌─────────────────────────────────────┐
         │  Data Drift Detection & Profiling   │
         │  (Schema changes, volume shifts)    │
         └────────────┬────────────────────────┘
                      │
                      ▼
         ┌─────────────────────────────────────┐
         │   Query Intent Routing              │
         │   (Load dynamic few-shot examples)  │
         └────────────┬────────────────────────┘
                      │
                      ▼
         ┌─────────────────────────────────────┐
         │   Specialized Agent Execution       │
         │   (Data/Root Cause/Validate/etc)    │
         └────────────┬────────────────────────┘
                      │
                      ▼
         ┌─────────────────────────────────────┐
         │   Evidence Grounding                │
         │   (Source reliability audit)        │
         └────────────┬────────────────────────┘
                      │
                      ▼
         ┌─────────────────────────────────────┐
         │   LLM Narrative Generation          │
         │   (Calibrated confidence scoring)   │
         └────────────┬────────────────────────┘
                      │
                      ▼
         ┌─────────────────────────────────────┐
         │   Planner Reviews Response          │
         │   Provides feedback/rating/correction│
         └────────────┬────────────────────────┘
                      │
                      ▼
    ┌─────────────────────────────────────────────┐
    │  Learning Loop: Record Outcome & Analytics   │
    │  ├─ Query Analytics: success patterns        │
    │  ├─ Confidence Calibration: audit scores     │
    │  ├─ Evidence Audit: source reliability       │
    │  ├─ Hallucination Detection: claim factuality│
    │  └─ Validation Tuning: rule effectiveness    │
    └─────────────────┬──────────────────────────┘
                      │
                      ▼
    ┌─────────────────────────────────────────────┐
    │  Continuous Optimization (Daily/Weekly)      │
    │  ├─ Rebuild few-shot examples               │
    │  ├─ Regenerate dynamic prompts              │
    │  ├─ Retune validation thresholds            │
    │  ├─ Update schema registry                   │
    │  └─ Alert team on data drift                │
    └─────────────────────────────────────────────┘
```

---

## Deployment and Monitoring

### 1. Self-Learning Services

```python
# webapp/app/services/__init__.py

class SelfLearningOrchestrator:
    """Coordinate all self-learning components."""
    
    def __init__(self):
        self.data_profiler = DataDriftDetector()
        self.query_analytics = QueryAnalytics()
        self.few_shot_learner = FewShotLearner()
        self.confidence_auditor = ConfidenceAuditor()
        self.evidence_tracker = EvidenceReliabilityTracker()
        self.prompt_optimizer = PromptOptimizer()
        self.hallucination_detector = HallucinationDetector()
        self.validation_tuner = ValidationTuner()
    
    async def process_response_outcome(self, response_record: dict):
        """Main entry point for recording query outcomes."""
        # Log query
        self.query_analytics.log_query(response_record)
        
        # Check hallucinations
        hallucinations = self.hallucination_detector.check_response_factuality(
            response_record,
            response_record.get("evidence", {})
        )
        
        if hallucinations["has_hallucinations"]:
            self.query_analytics.log_hallucinations(hallucinations)
        
        # Audit confidence
        self.confidence_auditor.audit_confidence(response_record)
        
        # Track evidence source
        self.evidence_tracker.record_evidence_outcome(response_record)
        
        # Add successful example if applicable
        if response_record.get("user_approved"):
            self.few_shot_learner.add_successful_example(response_record)
    
    async def daily_optimization(self):
        """Run daily to regenerate prompts and update rules."""
        # Analyze recent patterns
        patterns = self.query_analytics.analyze_patterns()
        
        # Get corrections
        corrections = self.query_analytics.get_correction_insights()
        
        # Regenerate prompts for each agent
        for intent in patterns.get("intent_patterns", {}).keys():
            optimized_prompt = self.prompt_optimizer.optimize_prompt_for_intent(
                intent,
                patterns
            )
            # Save optimized prompt
            self._save_agent_prompt(intent, optimized_prompt)
        
        # Tune validation rules
        rule_health = self.validation_tuner.get_rule_health()
        for rule_name, health in rule_health.items():
            if health["recommendation"] != "keep":
                adjustment = self.validation_tuner.auto_tune_thresholds(rule_name)
                self._apply_rule_adjustment(rule_name, adjustment)
        
        # Report on data drift
        drift_report = self._generate_drift_report()
        if drift_report.get("issues"):
            self._alert_team(drift_report)
```

### 2. Monitoring Dashboard Endpoint

```python
# webapp/app/routes/learning_analytics.py

@app.get("/api/learning/dashboard")
async def learning_dashboard():
    """Real-time view of self-learning health."""
    orchestrator = SelfLearningOrchestrator()
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "data_health": {
            "recent_drift": orchestrator.data_profiler.detect_drift(),
            "schema_versions": orchestrator.data_profiler.schemas.keys()
        },
        "query_patterns": orchestrator.query_analytics.analyze_patterns(),
        "confidence_calibration": orchestrator.confidence_auditor.get_calibration_report(),
        "evidence_reliability": orchestrator.evidence_tracker.get_source_reliability(),
        "validation_health": orchestrator.validation_tuner.get_rule_health(),
        "hallucination_rate": orchestrator.query_analytics.get_hallucination_rate(),
    }
```

---

## Best Practices

### 1. Data Governance
- Always maintain baseline fingerprints for comparison
- Version schema changes with drift context
- Keep 90-day rolling window of query logs
- Archive learned models monthly

### 2. Human-in-the-Loop
- Alert team when:
  - Data drift severity = "high"
  - Hallucination rate > 5%
  - Confidence calibration gap > 0.2
  - New entity types detected
- Require human approval for threshold changes > 20%

### 3. Confidence Thresholds
- Track confidence calibration continuously
- Adjust LLM response confidence if gap > 0.15
- Flag low-confidence responses for review
- Retrain few-shot examples weekly

### 4. Feedback Collection
- Always capture user feedback (approve/correct/reject)
- Log specific corrections for hallucination tracking
- Track response time and quality separately
- Monitor per-agent and per-intent metrics

---

## Summary

Self-learning in this system works at five levels:

| Level | What Changes | How Often | Impact |
|---|---|---|---|
| Data | Schema registry, entity detection | Real-time on load | High (affects all agents) |
| Query | Pattern library, few-shot examples | Daily | Medium (improves routing) |
| Evidence | Source reliability scores | Continuous | High (affects grounding) |
| LLM | Dynamic prompts, hallucination guards | Weekly | High (improves quality) |
| Validation | Rule thresholds and weights | Weekly | Medium (affects gates) |

This architecture allows your agents to adapt to evolving datasets, query patterns, and user feedback without manual retraining or prompt engineering.
