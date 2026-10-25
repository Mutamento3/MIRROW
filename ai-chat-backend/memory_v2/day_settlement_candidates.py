"""Deterministic candidate screening for completed active-day settlement.

The selector is intentionally only a recall-friendly sieve.  It may nominate
an edge for later judgment, but it cannot create a thread, assert continuity,
or infer resolution.  It reads event summaries and structural metadata only;
authoritative conversation bodies are outside this module's input.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Sequence

from .day_settlement import (
    MAX_CONTINUATION_CANDIDATES,
    DaySettlementPlan,
    SettlementContinuationCandidate,
    SettlementEvent,
    SettlementThreadCandidate,
    validate_day_settlement_plan,
)


CANDIDATE_SELECTOR_VERSION = "day-settlement-candidates-v2"
CANDIDATE_COVERAGE_VERSION = "day-settlement-candidates-v3"
CANDIDATE_COVERAGE_START_ACTIVE_DATE = "2026-10-07"
CANDIDATE_STABLE_SUPPORT_VERSION = 'day-settlement-candidates-v4-stable-support'
CANDIDATE_STABLE_SUPPORT_START_DATE = '2026-10-17'
CANDIDATE_ADJACENT_VERSION = 'day-settlement-candidates-v5-adjacent-exchange'
CANDIDATE_ADJACENT_START_DATE = '2026-10-19'
CANDIDATE_LONG_GAP_VERSION = 'day-settlement-candidates-v6-long-gap'
CANDIDATE_LONG_GAP_START_DATE = '2026-10-22'
CANDIDATE_NAMED_REFERENCE_VERSION = 'day-settlement-candidates-v7-named-reference'
CANDIDATE_NAMED_REFERENCE_START_DATE = '2026-10-23'
CANDIDATE_FULL_INVENTORY_VERSION = 'day-settlement-candidates-v8-full-inventory'
_LONG_GAP_SECONDS = 30 * 24 * 60 * 60
_LONG_GAP_BACK_REFERENCE_RE = re.compile(
    r"(?:上次|此前|之前|先前|那次|旧伤|旧事|复发|"
    r"接着(?:上次|之前)|继续(?:上次|之前)|"
    r"还没(?:好|完|解决|结束|兑现)|"
    r"仍未(?:好|完成|解决|结束|兑现))"
)
_CORE_PARTICIPANTS = {"human", "k"}
_GENERIC_EVENT_TYPES = {
    "conversation_moment",
    "interaction",
    "other",
    "status_update",
}
_GENERIC_TERMS = {
    "一个",
    "一些",
    "事情",
    "这件",
    "件事",
    "表示",
    "提到",
    "回应",
    "自己",
    "对话",
    "今天",
    "昨天",
    "现在",
    "开始",
    "发生",
    "吃了",
    "human",
    "assistant",
    "user",
    "人类伙伴",
    "伙伴",
    "伙伴",
}
_GENERIC_FRAGMENTS = (
    "人类伙伴",
    "人类伙伴",
    "表示",
    "提到",
    "回应",
    "说自己",
    "这件事情",
    "这件事",
)
_CONTINUATION_CUES = (
    "继续",
    "接着",
    "仍然",
    "还是",
    "还在",
    "再次",
    "复发",
    "上次",
)
_SIGNAL_PRIORITY = (
    "shared_named_reference",
    "shared_distinctive_terms",
    "shared_specific_participant",
    "explicit_continuation_cue",
    "same_primary_type",
    "close_in_time",
    "recent_prior_thread",
    "same_subject",
)


class DaySettlementCandidateError(ValueError):
    """The deterministic selector received an invalid plan or policy."""


@dataclass(frozen=True)
class DaySettlementCandidatePolicy:
    min_local_score: float = 0.38
    max_candidates_per_target: int = 3
    max_candidates_total: int = MAX_CONTINUATION_CANDIDATES
    include_same_batch: bool = False
    cover_targets: bool = False
    stable_intra_day_support: bool = False
    adjacent_private_exchange: bool = False
    allow_long_gap: bool = False
    named_reference_support: bool = False
    full_history_inventory: bool = False

    @classmethod
    def for_active_date(cls, active_date: str) -> "DaySettlementCandidatePolicy":
        coverage = active_date >= CANDIDATE_COVERAGE_START_ACTIVE_DATE
        return cls(include_same_batch=coverage, cover_targets=coverage,
                   stable_intra_day_support=active_date >= CANDIDATE_STABLE_SUPPORT_START_DATE,
                   adjacent_private_exchange=active_date >= CANDIDATE_ADJACENT_START_DATE,
                   allow_long_gap=active_date >= CANDIDATE_LONG_GAP_START_DATE,
                   named_reference_support=active_date >= CANDIDATE_NAMED_REFERENCE_START_DATE,
                   full_history_inventory=active_date >= '2026-10-23')

    def __post_init__(self) -> None:
        if any(type(value) is not bool for value in (self.include_same_batch, self.cover_targets,
                                                     self.stable_intra_day_support, self.adjacent_private_exchange, self.allow_long_gap, self.named_reference_support, self.full_history_inventory)):
            raise ValueError("candidate coverage switches must be boolean")
        if not 0 <= self.min_local_score <= 1:
            raise ValueError("min_local_score must be between 0 and 1")
        if self.max_candidates_per_target <= 0:
            raise ValueError("max_candidates_per_target must be positive")
        if not 1 <= self.max_candidates_total <= MAX_CONTINUATION_CANDIDATES:
            raise ValueError("max_candidates_total exceeds the settlement contract")


@dataclass(frozen=True)
class DaySettlementCandidateSelection:
    candidates: tuple[SettlementContinuationCandidate, ...]
    evaluated_pair_count: int
    eligible_pair_count: int
    dropped_by_bound_count: int
    selector_version: str = CANDIDATE_SELECTOR_VERSION
    deferred_candidates: tuple[SettlementContinuationCandidate, ...] = ()

    def safe_dict(self) -> dict[str, int | str | bool]:
        return {
            "candidate_count": len(self.candidates),
            "evaluated_pair_count": self.evaluated_pair_count,
            "eligible_pair_count": self.eligible_pair_count,
            "dropped_by_bound_count": self.dropped_by_bound_count,
            "deferred_candidate_count": len(self.deferred_candidates),
            "has_continuation_candidates": bool(self.candidates),
            "selector_version": self.selector_version,
        }

    def apply_to(self, plan: DaySettlementPlan) -> DaySettlementPlan:
        if plan.continuation_candidates:
            raise DaySettlementCandidateError(
                "candidate selection can only apply to an unscreened plan"
            )
        updated = replace(plan, continuation_candidates=self.candidates)
        validate_day_settlement_plan(updated)
        return updated


@dataclass(frozen=True)
class SettlementPairScreening:
    local_score: float
    signal_codes: tuple[str, ...]
    supported: bool

    def safe_dict(self) -> dict[str, float | list[str] | bool]:
        return {
            "local_score": self.local_score,
            "signal_codes": list(self.signal_codes),
            "supported": self.supported,
        }


@dataclass(frozen=True)
class _ScoredEdge:
    prior_thread_ref: str
    match_event_ref: str
    to_day_event_ref: str
    score: float
    signal_codes: tuple[str, ...]


def _timestamp(value: str) -> datetime:
    from .source_time import instant
    return instant(value)


def _lexical_terms(value: str) -> set[str]:
    lowered = str(value or "").casefold()
    for fragment in _GENERIC_FRAGMENTS:
        lowered = lowered.replace(fragment, " ")
    terms = {
        item
        for item in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered)
        if len(item) >= 3
    }
    for run in re.findall(r"[\u3400-\u9fff]+", lowered):
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms - _GENERIC_TERMS


def _specific_participants(event: SettlementEvent) -> set[str]:
    return {
        participant.strip().casefold()
        for participant in event.participant_ids
        if participant.strip().casefold() not in _CORE_PARTICIPANTS
    }


def _ordered_signals(signals: set[str]) -> tuple[str, ...]:
    return tuple(signal for signal in _SIGNAL_PRIORITY if signal in signals)[:4]


def has_long_gap_back_reference(summary: str) -> bool:
    """Whether a later event explicitly points back across a long time gap."""

    return bool(_LONG_GAP_BACK_REFERENCE_RE.search(str(summary or "")))


def is_long_gap_without_back_reference(
    earlier_reported_at: str,
    later_reported_at: str,
    later_summary: str,
) -> bool:
    """Identify unsafe month-scale continuation based only on generic similarity."""

    return bool(
        (_timestamp(later_reported_at) - _timestamp(earlier_reported_at)).total_seconds()
        > _LONG_GAP_SECONDS
        and not has_long_gap_back_reference(later_summary)
    )


def common_settlement_terms(
    events: Sequence[SettlementEvent],
) -> frozenset[str]:
    """Find corpus-template terms without suppressing small repeated event chains."""

    if not events:
        return frozenset()
    document_frequency = Counter(
        term for event in events for term in _lexical_terms(event.summary)
    )
    common_cutoff = max(4, math.ceil(len(events) * 0.10))
    return frozenset(
        term for term, count in document_frequency.items() if count > common_cutoff
    )


def screen_settlement_event_pair(
    earlier: SettlementEvent,
    later: SettlementEvent,
    *,
    intra_day: bool,
    ignored_terms: frozenset[str] = frozenset(),
    allow_long_gap: bool = False,
    named_reference_support: bool = False,
) -> SettlementPairScreening:
    """Return content-free local evidence without asserting continuation."""

    signals: set[str] = set()
    score = 0.0
    if (earlier.source_scope_ids and later.source_scope_ids
            and not set(earlier.source_scope_ids).intersection(later.source_scope_ids)):
        return SettlementPairScreening(0.0, (), False)

    from .state_changes import search_text
    earlier_terms = _lexical_terms(search_text(earlier.summary, earlier.state_changes)) - ignored_terms
    later_terms = _lexical_terms(search_text(later.summary, later.state_changes)) - ignored_terms
    shared_terms = earlier_terms & later_terms
    smaller_term_count = max(1, min(len(earlier_terms), len(later_terms)))
    overlap_ratio = len(shared_terms) / smaller_term_count
    if shared_terms:
        signals.add("shared_distinctive_terms")
        score += min(
            0.62,
            0.12 + 1.8 * overlap_ratio + 0.04 * min(len(shared_terms), 4),
        )

    same_type = bool(
        earlier.event_type
        and earlier.event_type.casefold() == later.event_type.casefold()
    )
    same_specific_type = bool(
        same_type and earlier.event_type.casefold() not in _GENERIC_EVENT_TYPES
    )
    if same_specific_type:
        signals.add("same_primary_type")
        score += 0.12
    if earlier.subject_id.casefold() == later.subject_id.casefold():
        signals.add("same_subject")
        score += 0.04

    shared_specific = _specific_participants(earlier) & _specific_participants(later)
    if shared_specific:
        signals.add("shared_specific_participant")
        score += 0.22

    explicit_cue = any(cue in later.summary for cue in _CONTINUATION_CUES)
    if explicit_cue:
        signals.add("explicit_continuation_cue")
        score += 0.18

    gap_seconds = (
        _timestamp(later.reported_at) - _timestamp(earlier.reported_at)
    ).total_seconds()
    if intra_day:
        if gap_seconds <= 30 * 60:
            signals.add("close_in_time")
            score += 0.10
        elif gap_seconds <= 6 * 60 * 60:
            signals.add("close_in_time")
            score += 0.05
    elif gap_seconds <= 7 * 24 * 60 * 60:
        signals.add("recent_prior_thread")
        score += 0.04

    if intra_day:
        lexical_support = bool(
            (
                same_specific_type
                and shared_terms
                and overlap_ratio >= 0.05
            )
            or (len(shared_terms) >= 4 and overlap_ratio >= 0.08)
        )
    else:
        lexical_support = bool(
            (
                same_specific_type
                and len(shared_terms) >= 2
                and overlap_ratio >= 0.08
            )
            or (len(shared_terms) >= 4 and overlap_ratio >= 0.18)
        )
    if allow_long_gap and not intra_day:
        # Broad interaction summaries dilute overlap and may change primary type.
        # This only opens the existing bounded semantic-review lane; it adds no edge.
        lexical_support |= len(shared_terms) >= 3 and overlap_ratio >= 0.05
    participant_support = bool(
        shared_specific and (same_specific_type or shared_terms)
    )
    supported = bool(
        lexical_support
        or participant_support
        or (explicit_cue and same_specific_type)
    )
    if named_reference_support and not intra_day:
        # A complete named work is a useful retrieval anchor even when it is
        # only one lexical token. It nominates review, never the same occurrence.
        titles = lambda text: {re.sub(r'\s+', ' ', title).strip().casefold()
            for title in re.findall(r'《([^《》\n]{2,80})》', text)}
        if titles(earlier.summary) & titles(later.summary):
            supported = True
            score = max(score, 0.62)
            signals.add('shared_named_reference')
    if (
        not intra_day and not allow_long_gap
        and is_long_gap_without_back_reference(
            earlier.reported_at,
            later.reported_at,
            later.summary,
        )
    ):
        supported = False
    return SettlementPairScreening(
        local_score=round(min(score, 1.0), 4),
        signal_codes=_ordered_signals(signals),
        supported=supported,
    )


def _candidate_ref(edge: _ScoredEdge) -> str:
    identity = "\0".join(
        (
            CANDIDATE_SELECTOR_VERSION,
            edge.prior_thread_ref,
            edge.match_event_ref,
            edge.to_day_event_ref,
        )
    )
    return "c" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _screen_prior_thread(
    thread: SettlementThreadCandidate,
    target: SettlementEvent,
    minimum_score: float,
    ignored_terms: frozenset[str],
    allow_long_gap: bool = False,
    named_reference_support: bool = False,
) -> tuple[_ScoredEdge | None, int]:
    best: tuple[float, datetime, SettlementEvent, tuple[str, ...]] | None = None
    evaluated = 0
    target_time = _timestamp(target.reported_at)
    # A thread can contain an older representative that resembles the target
    # while its append tail is actually later than the target.  The match node
    # is only retrieval evidence; a continuation edge must start at the real
    # tail, so such an overlap cannot produce a forward append candidate.
    if _timestamp(thread.representative_events[-1].reported_at) > target_time:
        return None, len(thread.representative_events)
    for representative in thread.representative_events:
        evaluated += 1
        if _timestamp(representative.reported_at) > target_time:
            continue
        screening = screen_settlement_event_pair(
            representative,
            target,
            intra_day=False,
            ignored_terms=ignored_terms,
            allow_long_gap=allow_long_gap,
            named_reference_support=named_reference_support,
        )
        if not screening.supported or screening.local_score < minimum_score:
            continue
        candidate = (
            screening.local_score,
            _timestamp(representative.reported_at),
            representative,
            screening.signal_codes,
        )
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        return None, evaluated
    return (
        _ScoredEdge(
            prior_thread_ref=thread.ref,
            match_event_ref=best[2].ref,
            to_day_event_ref=target.ref,
            score=best[0],
            signal_codes=best[3],
        ),
        evaluated,
    )


def select_day_settlement_candidates(
    plan: DaySettlementPlan,
    policy: DaySettlementCandidatePolicy | None = None,
) -> DaySettlementCandidateSelection:
    """Select bounded cross-batch and prior-thread candidates without a model."""

    if plan.continuation_candidates:
        raise DaySettlementCandidateError(
            "candidate selection requires an unscreened settlement plan"
        )
    from .settlement_candidate_pool import CandidatePool
    if isinstance(plan, CandidatePool):
        plan.validate()
    else:
        validate_day_settlement_plan(plan)
    policy = policy or DaySettlementCandidatePolicy.for_active_date(plan.active_date)

    day_events = sorted(
        plan.day_events,
        key=lambda event: (_timestamp(event.reported_at), event.ref),
    )
    edges_by_target: dict[str, list[_ScoredEdge]] = defaultdict(list)
    evaluated_pair_count = 0
    eligible_pair_count = 0
    ignored_terms = common_settlement_terms(
        (
            *day_events,
            *plan.prior_events,
            *(
                event
                for thread in plan.prior_threads
                for event in thread.representative_events
            ),
        )
    )

    for target_index, target in enumerate(day_events):
        for earlier in plan.prior_events:
            if _timestamp(earlier.reported_at) > _timestamp(target.reported_at):
                continue
            evaluated_pair_count += 1
            screening = screen_settlement_event_pair(
                earlier, target, intra_day=False, ignored_terms=ignored_terms, allow_long_gap=policy.allow_long_gap,
                named_reference_support=policy.named_reference_support,
            )
            if screening.supported and screening.local_score >= policy.min_local_score:
                eligible_pair_count += 1
                edges_by_target[target.ref].append(_ScoredEdge(
                    prior_thread_ref="", match_event_ref=earlier.ref,
                    to_day_event_ref=target.ref, score=screening.local_score,
                    signal_codes=screening.signal_codes,
                ))
        for earlier in day_events[:target_index]:
            if earlier.batch_id == target.batch_id and not policy.include_same_batch:
                continue
            evaluated_pair_count += 1
            screening = screen_settlement_event_pair(
                earlier,
                target,
                intra_day=True,
                ignored_terms=ignored_terms,
                allow_long_gap=policy.allow_long_gap,
                named_reference_support=policy.named_reference_support,
            )
            admitted = screening.supported and screening.local_score >= policy.min_local_score
            if policy.stable_intra_day_support and not admitted and screening.local_score >= policy.min_local_score:
                # A narrow recall repair: one suppressed shared term must not
                # discard a well-ranked same-day pair with three surviving
                # terms. Preserve every formerly admitted pair and every bound;
                # one generic common word alone never gains a new review call.
                raw_a, raw_b = _lexical_terms(earlier.summary), _lexical_terms(target.summary)
                shared = raw_a & raw_b
                admitted = (len(shared - ignored_terms) >= 3 and len(shared) >= 4
                            and len(shared) / max(1, min(len(raw_a), len(raw_b))) >= 0.08
                            and not (earlier.source_scope_ids and target.source_scope_ids
                                     and not set(earlier.source_scope_ids).intersection(target.source_scope_ids)))
            if admitted:
                eligible_pair_count += 1
                edges_by_target[target.ref].append(
                    _ScoredEdge(
                        prior_thread_ref="",
                        match_event_ref=earlier.ref,
                        to_day_event_ref=target.ref,
                        score=screening.local_score,
                        signal_codes=screening.signal_codes,
                    )
                )

        for thread in plan.prior_threads:
            edge, evaluated = _screen_prior_thread(
                thread,
                target,
                policy.min_local_score,
                ignored_terms,
                policy.allow_long_gap,
                policy.named_reference_support,
            )
            evaluated_pair_count += evaluated
            if edge is not None:
                eligible_pair_count += 1
                edges_by_target[target.ref].append(edge)

    if policy.adjacent_private_exchange:
        from .adjacent_exchange_candidates import nearest_private_predecessor, MAX_ADJACENT_CANDIDATES
        nominated = 0
        for index, target in enumerate(day_events):
            if edges_by_target[target.ref] or nominated >= MAX_ADJACENT_CANDIDATES:
                continue
            earlier = nearest_private_predecessor([e for e in day_events[:index]
                if policy.include_same_batch or e.batch_id != target.batch_id], target)
            if earlier is not None:
                # Local score is a scheduling rank, not a match confidence.
                edges_by_target[target.ref].append(_ScoredEdge('', earlier.ref, target.ref,
                    policy.min_local_score, ('adjacent_private_exchange',)))
                nominated += 1
                eligible_pair_count += 1

    per_target: list[_ScoredEdge] = []
    target_tiers: dict[tuple[str, str, str], int] = {}
    for target in day_events:
        ranked = sorted(
            edges_by_target.get(target.ref, ()),
            key=lambda edge: (
                -edge.score,
                edge.prior_thread_ref,
                edge.match_event_ref,
            ),
        )
        bounded = ranked[:policy.max_candidates_per_target]
        if policy.allow_long_gap and bounded:
            by_ref = {e.ref: e for e in (*plan.day_events, *plan.prior_events,
                *(e for t in plan.prior_threads for e in t.representative_events))}
            distant = next((edge for edge in ranked if
                (_timestamp(target.reported_at) - _timestamp(by_ref[edge.match_event_ref].reported_at)).total_seconds()
                > _LONG_GAP_SECONDS), None)
            # Reserve one existing seat for the strongest distant candidate.
            # Nearby generic interaction matches cannot occupy every seat.
            if distant is not None and distant not in bounded:
                bounded[-1] = distant
        for tier, edge in enumerate(bounded):
            per_target.append(edge)
            target_tiers[(edge.prior_thread_ref, edge.match_event_ref, edge.to_day_event_ref)] = tier

    # Within each target-choice round, consider distinct recurring families
    # before a second target from the same family. This only distributes model
    # judgment seats; it neither merges occurrences nor declares a pair false.
    day_by_ref = {event.ref: event for event in day_events}
    family_positions = {}
    family_counts = Counter()
    for edge in sorted(per_target, key=lambda edge: (-edge.score, edge.to_day_event_ref, edge.match_event_ref)):
        identity = (edge.prior_thread_ref, edge.match_event_ref, edge.to_day_event_ref)
        target = day_by_ref[edge.to_day_event_ref]
        family = ((target.recurrence_label, target.subject_id, tuple(sorted(target.participant_ids)),
                   tuple(sorted(target.source_scope_ids))) if target.recurrence_label else (target.ref,))
        round_family = (target_tiers[identity], family)
        family_positions[identity] = family_counts[round_family]
        family_counts[round_family] += 1

    globally_bounded = sorted(
        per_target,
        key=lambda edge: (
            target_tiers[(edge.prior_thread_ref, edge.match_event_ref, edge.to_day_event_ref)]
            if policy.cover_targets else 0,
            family_positions[(edge.prior_thread_ref, edge.match_event_ref, edge.to_day_event_ref)]
            if policy.cover_targets else 0,
            -edge.score, edge.to_day_event_ref, edge.match_event_ref,
        ),
    )[: policy.max_candidates_total]
    target_order = {event.ref: index for index, event in enumerate(day_events)}
    selected_edges = sorted(
        globally_bounded,
        key=lambda edge: (
            target_order[edge.to_day_event_ref],
            -edge.score,
            edge.prior_thread_ref,
            edge.match_event_ref,
        ),
    )
    def project(edge: _ScoredEdge) -> SettlementContinuationCandidate:
        return SettlementContinuationCandidate(
            ref=_candidate_ref(edge), prior_thread_ref=edge.prior_thread_ref,
            match_event_ref=edge.match_event_ref, to_day_event_ref=edge.to_day_event_ref,
            local_score=round(edge.score, 4), signal_codes=edge.signal_codes,
        )

    candidates = tuple(project(edge) for edge in selected_edges)
    selected_refs = {candidate.ref for candidate in candidates}
    # Preserve identities, not just a count: a budget exclusion is not a
    # semantic rejection. Includes both per-target and per-call exclusions.
    deferred = tuple(
        project(edge)
        for target in day_events
        for edge in sorted(edges_by_target[target.ref], key=lambda edge: (
            -edge.score, edge.prior_thread_ref, edge.match_event_ref))
        if _candidate_ref(edge) not in selected_refs
    ) if policy.cover_targets else ()
    selection = DaySettlementCandidateSelection(
        candidates=candidates,
        evaluated_pair_count=evaluated_pair_count,
        eligible_pair_count=eligible_pair_count,
        dropped_by_bound_count=eligible_pair_count - len(candidates),
        selector_version=(CANDIDATE_FULL_INVENTORY_VERSION if isinstance(plan, CandidatePool) else
                          CANDIDATE_NAMED_REFERENCE_VERSION if policy.named_reference_support else
                          CANDIDATE_LONG_GAP_VERSION if policy.allow_long_gap else
                          CANDIDATE_ADJACENT_VERSION if policy.adjacent_private_exchange else
                          CANDIDATE_STABLE_SUPPORT_VERSION if policy.stable_intra_day_support else
                          CANDIDATE_COVERAGE_VERSION if policy.include_same_batch or policy.cover_targets
                          else CANDIDATE_SELECTOR_VERSION),
        deferred_candidates=deferred,
    )
    if not isinstance(plan, CandidatePool):
        selection.apply_to(plan)
    return selection
