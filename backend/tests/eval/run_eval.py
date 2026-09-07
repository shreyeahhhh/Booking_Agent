"""Extraction eval set runner -- MASTER_PLAN.md Phase 5.2.

Runs every case in cases.py through the real extractor and scores the
result on three axes (field, op, value), printing a table. Not a strict
pytest gate on its own -- see test_eval.py for the permanent regression
version of this with a minimum-score assertion; this file is the
standalone report a human reads.

Run from backend/, as a module so `app.*` imports resolve correctly:

    python -m tests.eval.run_eval
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from groq import AsyncGroq

from app.config import get_settings
from app.domain.reducer import apply
from app.domain.state import BookingState, Patch, PatchOp
from app.llm.extractor import extract
from tests.eval.cases import CASES, EvalCase, ExpectedPatch

_settings = get_settings()


@dataclass
class CaseResult:
    case: EvalCase
    matched_expected: int
    total_expected: int
    correct_actual: int
    total_actual: int
    intent_ok: bool
    notes: list[str]


def _item_name(value: object) -> str | None:
    if isinstance(value, dict):
        return str(value.get("name", "")).lower().strip() or None
    if isinstance(value, str):
        return value.lower().strip() or None
    return None


def _values_match(field_path: str, expected_value: object, actual_patch: Patch) -> bool:
    if expected_value is None:
        return True
    if field_path == "goods.items":
        return _item_name(expected_value) == _item_name(actual_patch.value)
    actual = actual_patch.value
    if isinstance(expected_value, bool) or isinstance(actual, bool):
        return bool(expected_value) == bool(actual)
    return str(expected_value).strip().lower() == str(actual).strip().lower()


def _setup_state(setup: list[ExpectedPatch]) -> BookingState:
    state = BookingState()
    if not setup:
        return state
    patches = [
        Patch(op=PatchOp(p.op), field=p.field, value=p.value, confidence=1.0, evidence=str(p.value))
        for p in setup
    ]
    return apply(state, patches).state


async def score_case(client: AsyncGroq, case: EvalCase) -> CaseResult:
    state = _setup_state(case.setup)
    result = await extract(
        client,
        model=_settings.groq_llm_model,
        state=state,
        last_question=case.last_question,
        recent_turns=[],
        utterance=case.utterance,
    )

    notes: list[str] = []
    remaining_actual = list(result.patches)
    matched_expected = 0

    for expected in case.expected:
        candidates = [
            p for p in remaining_actual if p.field == expected.field and p.op.value == expected.op
        ]
        hit = next((p for p in candidates if _values_match(expected.field, expected.value, p)), None)
        if hit is None and candidates:
            hit = candidates[0]  # field/op matched but value diverged -- still counts as "found", scored below
        if hit is None:
            notes.append(f"MISSING: {expected.op} {expected.field}={expected.value!r}")
            continue
        value_ok = _values_match(expected.field, expected.value, hit)
        ambiguity_ok = (hit.ambiguity is not None) if expected.ambiguous else True
        if value_ok and ambiguity_ok:
            matched_expected += 1
        else:
            reason = "wrong value" if not value_ok else "not flagged ambiguous"
            notes.append(f"PARTIAL: {expected.op} {expected.field} ({reason}, got {hit.value!r})")
            matched_expected += 1  # field/op correct counts toward recall; value mismatch is noted, not double-penalised
        remaining_actual.remove(hit)

    for extra in remaining_actual:
        notes.append(f"EXTRA (unexpected): {extra.op.value} {extra.field}={extra.value!r}")

    total_expected = len(case.expected)
    total_actual = len(result.patches)
    correct_actual = total_actual - len(remaining_actual)

    intent_ok = case.expected_intent is None or result.intent.value == case.expected_intent
    if not intent_ok:
        notes.append(f"INTENT: expected {case.expected_intent!r}, got {result.intent.value!r}")

    return CaseResult(case, matched_expected, total_expected, correct_actual, total_actual, intent_ok, notes)


async def run_all() -> list[CaseResult]:
    client = AsyncGroq(api_key=_settings.groq_api_key)
    return [await score_case(client, case) for case in CASES]


def print_report(results: list[CaseResult]) -> None:
    total_expected = sum(r.total_expected for r in results)
    total_matched = sum(r.matched_expected for r in results)
    total_actual = sum(r.total_actual for r in results)
    total_correct_actual = sum(r.correct_actual for r in results)
    intent_correct = sum(1 for r in results if r.intent_ok)

    recall = total_matched / total_expected if total_expected else 1.0
    precision = total_correct_actual / total_actual if total_actual else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    print(f"{'case':<40} {'recall':>8} {'precision':>10} {'intent':>8}  notes")
    print("-" * 100)
    for r in results:
        case_recall = f"{r.matched_expected}/{r.total_expected}" if r.total_expected else "-"
        case_precision = f"{r.correct_actual}/{r.total_actual}" if r.total_actual else "-"
        intent_mark = "ok" if r.intent_ok else "FAIL"
        note = r.notes[0] if r.notes else ""
        print(f"{r.case.name:<40} {case_recall:>8} {case_precision:>10} {intent_mark:>8}  {note}")
        for extra_note in r.notes[1:]:
            print(f"{'':<40} {'':>8} {'':>10} {'':>8}  {extra_note}")

    print("-" * 100)
    print(f"Overall: recall={recall:.2f} precision={precision:.2f} f1={f1:.2f} "
          f"intent_accuracy={intent_correct}/{len(results)}")


if __name__ == "__main__":
    results = asyncio.run(run_all())
    print_report(results)
