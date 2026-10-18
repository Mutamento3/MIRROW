"""Regression coverage for the public autonomous goal contract."""

import tempfile
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from wander_manager.event_types import EventType
from wander_manager.goal_policy import normalize_extension, normalize_goal_policy
from wander_manager.plan_decision_adapter import ActivitySpec, PlanDecision
from wander_manager.runtime_controller import RuntimeActionType, WanderRuntimeController
from wander_manager.runtime_models import GoalMode, WanderRun
from wander_manager.runtime_store import WanderRuntimeStore


class PublicGoalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WanderRuntimeStore(Path(self.tmp.name) / "runtime.db")
        self.store.initialize()
        self.now = datetime(2026, 10, 18, 12, 0, 0)
        self.controller = WanderRuntimeController(self.store, clock=lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def test_estimated_count_can_be_explicitly_extended_and_is_persisted_atomically(self):
        run = WanderRun(session_id="test")
        self.store.save_run(run)
        plan = PlanDecision(
            (ActivitySpec(EventType.LISTEN_MUSIC, GoalMode.COUNT, 1, "test", "estimate"),),
            30,
            {},
            autonomous_goals=True,
        )
        activity = self.controller.apply_plan(run, plan)[0]
        node = self.controller.begin_node(activity.activity_id)

        action = self.controller.complete_node_review(
            node.node_id,
            continue_activity=True,
            extend_count=2,
            goal_reason="new material",
        )

        self.assertEqual(RuntimeActionType.WAIT, action.action)
        persisted = self.store.get_activity(activity.activity_id)
        self.assertEqual(3, persisted["goal_value"])
        self.assertEqual("completed", self.store.get_node(node.node_id)["state"])
        snapshot = self.store.get_run(run.run_id)["context_snapshot"]
        policy = snapshot["autonomy"]["activity_goal_policies"][activity.activity_id]
        self.assertEqual(2, policy["extensions_total"])
        self.assertEqual("new material", policy["reason"])

    def test_rejects_impossible_extension_values(self):
        self.assertEqual(0, normalize_extension(2**63))
        self.assertEqual(0, normalize_extension(-1))
        self.assertEqual(0, normalize_extension("not-an-integer"))
        self.assertEqual(0, normalize_extension(True))
        self.assertEqual(0, normalize_extension(1.5))

    def start(self, *, autonomous=True, completion="estimate", trigger="idle",
              event=EventType.LISTEN_MUSIC, mode=GoalMode.COUNT, value=1):
        run = WanderRun(session_id="test", trigger_reason=trigger)
        self.store.save_run(run)
        plan = PlanDecision((ActivitySpec(event, mode, value, "test", completion),),
                            30, {}, autonomous_goals=autonomous)
        activity = self.controller.apply_plan(run, plan)[0]
        return run, activity, self.controller.begin_node(activity.activity_id)

    def test_count_estimate_requires_explicit_positive_extension(self):
        _, activity, node = self.start()
        result = self.controller.complete_node_review(node.node_id, continue_activity=True)
        self.assertEqual(RuntimeActionType.SETTLE, result.action)
        self.assertEqual(1, self.store.get_activity(activity.activity_id)["goal_value"])

    def test_fixed_and_legacy_and_chat_tasks_cannot_extend(self):
        for options in ({"completion": "fixed"}, {"autonomous": False}, {"trigger": "chat_tool"}):
            with self.subTest(options=options):
                _, activity, node = self.start(**options)
                result = self.controller.complete_node_review(node.node_id, extend_count=4)
                self.assertEqual(RuntimeActionType.SETTLE, result.action)
                self.assertEqual(1, self.store.get_activity(activity.activity_id)["goal_value"])

    def test_deadline_does_not_extend_or_create_successor(self):
        _, activity, node = self.start()
        self.now += timedelta(minutes=31)
        result = self.controller.complete_node_review(node.node_id, extend_count=4,
            switch_to=(EventType.MEMORY_FETCH, "another activity"))
        self.assertEqual("plan_horizon", result.reason.value)
        self.assertEqual(1, self.store.get_activity(activity.activity_id)["goal_value"])
        self.assertEqual(1, len(self.store.list_activities(activity.run_id)))

    def test_interrupt_and_failure_stop_without_extension_or_successor(self):
        for interrupted in (True, False):
            _, activity, node = self.start()
            if interrupted:
                self.controller.request_interrupt(activity.activity_id)
            result = self.controller.complete_node_review(node.node_id, extend_count=4,
                execution_failed=not interrupted,
                switch_to=(EventType.MEMORY_FETCH, "another activity"))
            self.assertEqual(RuntimeActionType.SETTLE, result.action)
            self.assertEqual(1, self.store.get_activity(activity.activity_id)["goal_value"])
            self.assertEqual(1, len(self.store.list_activities(activity.run_id)))

    def test_open_ended_estimate_has_no_legacy_node_ceiling(self):
        self.controller.max_open_nodes = 1
        for autonomous in (True, False):
            _, _, node = self.start(autonomous=autonomous, event=EventType.MEMORY_FETCH,
                                    mode=GoalMode.OPEN_ENDED, value=None)
            action = self.controller.complete_node_review(node.node_id)
            self.assertEqual(RuntimeActionType.WAIT if autonomous else RuntimeActionType.SETTLE,
                             action.action)

    def test_switch_goal_and_restart_keep_policy(self):
        run, activity, node = self.start()
        self.controller.complete_node_review(node.node_id, continue_activity=False,
            switch_to=(EventType.BROWSE_NEWS, "new topic"),
            switch_goal={"mode": "count", "value": 40, "completion": "estimate"})
        restarted = WanderRuntimeStore(Path(self.tmp.name) / "runtime.db")
        successor = restarted.list_activities(run.run_id)[1]
        self.assertEqual(40, successor["goal_value"])
        policy = restarted.get_run(run.run_id)["context_snapshot"]["autonomy"]["activity_goal_policies"][successor["activity_id"]]
        self.assertEqual("estimate", policy["completion"])
        with self.assertRaises(ValueError):
            self.controller.complete_node_review(node.node_id, extend_count=10)
        self.assertEqual(2, len(restarted.list_activities(run.run_id)))

    def test_failed_node_commit_rolls_back_run_activity_and_extension(self):
        run, activity, node = self.start()
        before = (self.store.get_run(run.run_id), self.store.get_activity(activity.activity_id),
                  self.store.get_node(node.node_id))
        # Inject a database failure in the final write of the combined transaction.
        with closing(sqlite3.connect(Path(self.tmp.name) / "runtime.db")) as conn:
            table = next(row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                         if row[0].endswith("nodes"))
            conn.execute(f"CREATE TRIGGER fail_node_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.controller.complete_node_review(node.node_id, extend_count=2)
        after = (self.store.get_run(run.run_id), self.store.get_activity(activity.activity_id),
                 self.store.get_node(node.node_id))
        self.assertEqual(before, after)

    def test_count_normalization_retains_catalog_mode_conversion(self):
        _, autonomous_count, _ = normalize_goal_policy(EventType.BROWSE_NEWS,
            {"mode": "count", "value": 40}, autonomous=True)
        _, legacy_count, _ = normalize_goal_policy(EventType.BROWSE_NEWS,
            {"mode": "count", "value": 40}, autonomous=False)
        _, converted, _ = normalize_goal_policy(EventType.BROWSE_NEWS,
            {"mode": "duration", "value": 20}, autonomous=True)
        self.assertEqual((40, 10, 4), (autonomous_count, legacy_count, converted))
