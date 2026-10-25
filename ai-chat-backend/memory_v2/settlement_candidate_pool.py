"""Local screening inventory, separate from the bounded provider page contract."""
from dataclasses import dataclass, replace
import json

from .day_settlement import (
    DaySettlementPlan, SettlementThreadCandidate, validate_day_settlement_plan,
    DaySettlementContractError, MAX_PRIOR_THREADS, MAX_PRIOR_EVENTS,
    MAX_REPRESENTATIVE_EVENTS_PER_THREAD, MAX_CONTINUATION_CANDIDATES, MAX_DAY_EVENTS,
    _validate_event, _validate_ref, _timestamp,
)

ASSEMBLER_VERSION = 'day-settlement-assembler-v3-full-inventory'


@dataclass(frozen=True)
class CandidatePool:
    source_namespace: str
    active_date: str
    source_digest: str
    day_events: tuple
    prior_threads: tuple
    prior_events: tuple
    continuation_candidates: tuple = ()

    def validate(self):
        validate_day_settlement_plan(DaySettlementPlan(
            self.source_namespace, self.active_date, self.source_digest, self.day_events))
        refs = {e.ref for e in self.day_events}
        ids = {e.event_id for e in self.day_events}
        thread_refs, thread_ids = set(), set()
        for thread in self.prior_threads:
            _validate_ref(thread.ref, 'thread ref')
            if not thread.thread_id or thread.ref in thread_refs or thread.thread_id in thread_ids:
                raise DaySettlementContractError('duplicate pool thread identity')
            thread_refs.add(thread.ref)
            thread_ids.add(thread.thread_id)
            if not thread.representative_events:
                raise DaySettlementContractError('empty pool thread')
            times = [_timestamp(e.reported_at) for e in thread.representative_events]
            if times != sorted(times):
                raise DaySettlementContractError('pool thread nodes must be chronological')
        for event in (*self.prior_events, *(e for t in self.prior_threads for e in t.representative_events)):
            _validate_event(event, label='inventory event')
            if event.ref in refs or event.event_id in ids:
                raise DaySettlementContractError('duplicate pool event identity')
            refs.add(event.ref)
            ids.add(event.event_id)

    def candidate_nodes(self, candidate):
        thread = next(t for t in self.prior_threads if t.ref == candidate.prior_thread_ref)
        nodes = thread.representative_events
        wanted = {nodes[0].ref, candidate.match_event_ref, nodes[-1].ref}
        return tuple(e for e in nodes if e.ref in wanted)

    def materialize(self, selection):
        """Keep all excluded candidates in the inventory, never omit a match."""
        threads, prior_refs, selected, deferred = {}, set(), [], []
        prior = {e.ref: e for e in self.prior_events}
        by_ref = {t.ref: t for t in self.prior_threads}
        for candidate in selection.candidates:
            if candidate.prior_thread_ref:
                ref = candidate.prior_thread_ref
                old = threads.get(ref, ())
                wanted = {e.ref for e in (*old, *self.candidate_nodes(candidate))}
                if len(wanted) > MAX_REPRESENTATIVE_EVENTS_PER_THREAD or (
                    ref not in threads and len(threads) >= MAX_PRIOR_THREADS
                ):
                    deferred.append(candidate)
                    continue
                threads[ref] = tuple(e for e in by_ref[ref].representative_events if e.ref in wanted)
            elif candidate.match_event_ref in prior:
                if candidate.match_event_ref not in prior_refs and len(prior_refs) >= MAX_PRIOR_EVENTS:
                    deferred.append(candidate)
                    continue
                prior_refs.add(candidate.match_event_ref)
            selected.append(candidate)
        updated = replace(selection, candidates=tuple(selected),
                          deferred_candidates=tuple(deferred) + selection.deferred_candidates,
                          dropped_by_bound_count=selection.eligible_pair_count - len(selected))
        plan = DaySettlementPlan(
            self.source_namespace, self.active_date, self.source_digest, self.day_events,
            prior_threads=tuple(SettlementThreadCandidate(ref, by_ref[ref].thread_id, nodes)
                                for ref, nodes in threads.items()),
            prior_events=tuple(e for e in self.prior_events if e.ref in prior_refs),
            continuation_candidates=tuple(selected),
        )
        validate_day_settlement_plan(plan)
        return plan, updated


def pack_pending_rows(rows):
    """Pack only unissued rows. Frozen requests are restored in their own order."""
    selected, threads, day_ids, prior_ids = [], {}, set(), set()
    for row in rows:
        if len(selected) >= MAX_CONTINUATION_CANDIDATES:
            break
        context = tuple(json.loads(row['context_event_ids_json']))
        thread_id = row['prior_thread_id'] or ''
        proposed_day = day_ids | {row['to_event_id']}
        proposed_prior = set(prior_ids)
        if thread_id:
            nodes = set(context) - {row['to_event_id']}
            previous = threads.get(thread_id)
            if previous and previous[0] != row['from_event_id']:
                continue
            merged = nodes | (previous[1] if previous else set())
            if len(merged) > MAX_REPRESENTATIVE_EVENTS_PER_THREAD or (
                not previous and len(threads) >= MAX_PRIOR_THREADS
            ):
                continue
        else:
            # Source dates are checked during projection. Count standalone
            # endpoints conservatively against the smaller prior-event bound.
            proposed_prior.update(set(context) - {row['to_event_id']})
        if len(proposed_day | proposed_prior) > MAX_DAY_EVENTS or len(proposed_prior) > MAX_PRIOR_EVENTS:
            continue
        if thread_id:
            threads[thread_id] = (row['from_event_id'], merged)
        selected.append(row)
        day_ids, prior_ids = proposed_day, proposed_prior
    return selected
