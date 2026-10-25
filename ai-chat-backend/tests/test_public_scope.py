"""Synthetic release-boundary tests: no app startup, model, device or data reads."""
import ast
import asyncio
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_admission():
    spec = importlib.util.spec_from_file_location('isolated_body_admission', ROOT / 'body_admission.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicScopeTests(unittest.TestCase):
    def test_social_host_requires_explicit_configuration(self):
        spec = importlib.util.spec_from_file_location('isolated_social_host', ROOT / 'social_feed/host_runtime.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIsNone(module.current_runtime())
        with self.assertRaisesRegex(RuntimeError, 'not_configured'):
            module.require_runtime()
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as exc:
            module.friends()
        self.assertEqual(exc.exception.status_code, 503)
        self.assertEqual(module.cognition_profile('synthetic-actor'), {})

    def test_admission_blocks_new_work_and_releases(self):
        module = load_admission()
        token = module.reserve(require_idle=True, direction='social')
        with self.assertRaises(RuntimeError):
            module.enter_work()
        module.release(token)
        lease = module.enter_work()
        module.leave_work(lease)
        self.assertFalse(module.status()['busy'])
        self.assertEqual(module.status()['ordinary_work_count'], 0)

    def test_reserved_owner_can_finish_its_own_work(self):
        module = load_admission()
        token = module.reserve(direction='workshop')
        with module.owned(token):
            self.assertIsNone(module.enter_work())
        module.release(token)

    def test_pending_work_is_not_discarded(self):
        module = load_admission()
        async def scenario():
            token = module.reserve(direction='xhs')
            calls = []
            @module.ordinary
            async def work():
                calls.append('executed')
            task = asyncio.create_task(work())
            await asyncio.sleep(0)
            self.assertEqual(calls, [])
            module.release(token)
            await asyncio.wait_for(task, 1)
            self.assertEqual(calls, ['executed'])
        asyncio.run(scenario())

    def test_daily_lanes_are_four_and_have_four_steps(self):
        tree = ast.parse((ROOT / 'cognition/daily.py').read_text(encoding='utf-8'))
        names = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'names' for t in node.targets)]
        self.assertEqual(ast.literal_eval(names[0].value), ('world', 'self', 'interest', 'other'))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'zip'
                 and isinstance(node.args[0], ast.Name) and node.args[0].id == 'names'
                 and isinstance(node.args[1], ast.Tuple)]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0].args[1].elts), 4)

    def test_no_private_peer_or_excluded_wander_events(self):
        tree = ast.parse((ROOT / 'wander_manager/event_types.py').read_text(encoding='utf-8'))
        names = {n.targets[0].id for c in tree.body if isinstance(c, ast.ClassDef) and c.name == 'EventType'
                 for n in c.body if isinstance(n, ast.Assign)}
        self.assertFalse(names & {'K_PEER_CHAT', 'VISIT_LOUNGE', 'REPLY_HOME_BOARD', 'FEDERATED_ROOM_CHAT', 'BROWSE_TAOBAO'})
        self.assertTrue({'PLAY_GAME', 'BROWSE_SOCIAL_FEED', 'LISTEN_MUSIC', 'SELF_REFLECTION'} <= names)


if __name__ == '__main__':
    unittest.main()
