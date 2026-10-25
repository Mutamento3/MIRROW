"""Independent-review regressions using synthetic state only."""
import ast
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_file(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ReviewRepairs(unittest.TestCase):
    def test_camera_failure_never_probes_another_device(self):
        tree = ast.parse((ROOT / 'behavior_scheduler/camera_tool.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        for opens in (False, True):
            seen, released = [], []
            class Capture:
                def isOpened(self): return opens
                def set(self, *args): pass
                def read(self): return False, None
                def release(self): released.append(True)
            def capture(index): seen.append(index); return Capture()
            async def no_sleep(seconds): pass
            namespace = dict(BaseTool=object, Optional=__import__('typing').Optional,
                ToolResult=lambda **kw: SimpleNamespace(**kw), ToolStatus=SimpleNamespace(ERROR='error'),
                os=os, CV2_AVAILABLE=True, PIL_AVAILABLE=True,
                logger=SimpleNamespace(info=lambda *a: None, error=lambda *a: None),
                cv2=SimpleNamespace(VideoCapture=capture, CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4))
            exec(compile(ast.Module(body=[cls], type_ignores=[]), '<camera>', 'exec'), namespace)
            with patch('asyncio.sleep', no_sleep):
                result = asyncio.run(namespace['CameraTool']().execute(device_index=7))
            self.assertEqual(result.status, 'error')
            self.assertEqual(seen, [7])
            self.assertEqual(released, [True])

    def test_embedded_tool_dispatch_calls_once(self):
        tree = ast.parse((ROOT / 'behavior_scheduler/agent_tools_lib/src/tools/tool_manager.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'execute_tool')
        namespace = dict(inspect=__import__('inspect'), Any=object)
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<dispatch>', 'exec'), namespace)
        calls = []
        class DictTool:
            def run(self, input): calls.append(input); return input
        class IdTool:
            def run(self, tool_call_id, **kwargs): calls.append(tool_call_id); return kwargs
        class FailingTool:
            def run(self, input): calls.append(input); raise TypeError('execution failure')
        dispatch = namespace['execute_tool']
        for tool in (DictTool(), IdTool()):
            self.assertEqual(dispatch(SimpleNamespace(get_tool=lambda name: tool), 'call', 'tool', query='test'), {'query': 'test'})
        with self.assertRaises(TypeError):
            dispatch(SimpleNamespace(get_tool=lambda name: FailingTool()), 'call', 'tool', query='test')
        self.assertEqual(len(calls), 3)

    def test_workshop_templates_match_public_capabilities(self):
        module = load_file('release_templates', 'workshop/creative_templates.py')
        for key in ('journey_card', 'gift_card', 'mood_picture'):
            self.assertIsNone(module.find(key))
            with self.assertRaises(ValueError): module.invitation_content(key, 'synthetic')
        for key in ('body_widget', 'interactive_gift', 'tiny_game', 'daily_tool', 'existing_upgrade', 'keepsake', 'tiny_interaction'):
            self.assertIsNotNone(module.find(key))
            self.assertTrue(module.invitation_content(key, 'synthetic'))
        self.assertIn('不包含模型生图通路', module.guidance())
        for relative in ('workshop/autonomy.py', 'workshop/requirements.py', 'workshop/creative_templates.py'):
            source = (ROOT / relative).read_text(encoding='utf-8')
            for excluded in ('character_anchors', '走日常生图', '调用可用通路', '图片若需要则真实生成', '原生图片自动交付'):
                self.assertNotIn(excluded, source)

    def test_embedded_tool_manager_constructor_contract(self):
        base = ROOT / 'behavior_scheduler/agent_tools_lib/src/tools'
        tree = ast.parse((base / 'tool_manager.py').read_text(encoding='utf-8'))
        for name, file in (('WebSearchTool', 'web_search_tool.py'), ('WebBrowserTool', 'web_browser_tool.py')):
            definition = ast.parse((base / file).read_text(encoding='utf-8'))
            cls = next(n for n in definition.body if isinstance(n, ast.ClassDef) and n.name == name)
            init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
            parameters = {arg.arg for arg in init.args.args[1:]}
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name]
            self.assertTrue(calls)
            for call in calls:
                self.assertTrue({kw.arg for kw in call.keywords} <= parameters)
                self.assertEqual(call.args, [])

    def test_empty_network_policy_is_loopback_only(self):
        module = load_file('release_network', 'network_access.py')
        policy = module.NetworkAccessPolicy(frozenset())
        self.assertTrue(policy.allows('127.0.0.1'))
        self.assertTrue(policy.allows('::1'))
        self.assertFalse(policy.allows('192.0.2.20'))
        self.assertFalse(policy.allows(None))
        explicit = module.NetworkAccessPolicy(frozenset({'192.0.2.20'}))
        self.assertTrue(explicit.allows('192.0.2.20'))
        self.assertFalse(explicit.allows('192.0.2.21'))

    def test_allowed_ips_default(self):
        tree = ast.parse((ROOT / 'shared_state.py').read_text(encoding='utf-8'))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'resolve_allowed_ips')
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<isolated>', 'exec'), namespace)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(namespace['resolve_allowed_ips'](), {'127.0.0.1', '::1'})

    def test_optional_public_host_import_and_activation(self):
        for value in ('', 'https://example.org', 'example.org:443', 'example.org.'):
            with patch.dict(os.environ, {'COMMENT_PUBLIC_HOST': value}, clear=True):
                module = load_file('release_social_config', 'social_feed/deployment_config.py')
                with self.assertRaises(RuntimeError):
                    module.require_public_configuration()
        with patch.dict(os.environ, {'COMMENT_PUBLIC_HOST': 'example.org'}, clear=True):
            module = load_file('release_social_config', 'social_feed/deployment_config.py')
            module.require_public_configuration()
            self.assertEqual(module.PUBLIC_SOCIAL_ORIGIN, 'https://example.org')
        tree = ast.parse((ROOT / 'social_feed/public_gateway.py').read_text(encoding='utf-8'))
        factory = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'create_public_social_app')
        self.assertEqual(factory.body[1].value.func.id, 'require_public_configuration')

    def test_direct_start_configures_routes_before_server(self):
        tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
        start = next(i for i, n in enumerate(tree.body) if isinstance(n, ast.If) and '__name__' in ast.unparse(n.test))
        preceding = ast.unparse(ast.Module(body=tree.body[:start], type_ignores=[]))
        self.assertIn('configure_ui_actions(conversation_manager', preceding)
        self.assertIn('app.include_router(optional_home_router)', preceding)
        self.assertIn("os.getenv('HOST', '127.0.0.1')", ast.unparse(tree.body[start]))
        self.assertEqual(start, len(tree.body) - 1)

    def test_unconfigured_camera_does_not_open_device(self):
        tree = ast.parse((ROOT / 'behavior_scheduler/camera_tool.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        namespace = dict(BaseTool=object, Optional=__import__('typing').Optional,
                         ToolResult=lambda **kw: SimpleNamespace(**kw),
                         ToolStatus=SimpleNamespace(ERROR='error'), os=os)
        exec(compile(ast.Module(body=[cls], type_ignores=[]), '<camera>', 'exec'), namespace)
        # No cv2 or device API exists in this namespace: the guard must run first.
        with patch.dict(os.environ, {}, clear=True):
            result = asyncio.run(namespace['CameraTool']().execute())
        self.assertEqual(result.status, 'error')
        self.assertIn('CAMERA_DEVICE_INDEX', result.error)

    def _invitation(self, failure=None):
        module = load_file('release_invitation', 'ui_action_messages.py')
        calls, rows = [], {}
        session = SimpleNamespace(messages=[])
        async def current(): return 'synthetic-session'
        async def get_session(sid): return session
        async def add(sid, role, content, message_id, event_type):
            calls.append('write')
            if failure == 'write': raise RuntimeError('write failed')
            if failure != 'missing':
                rows[message_id] = dict(session_id=sid, role=role, event_type=event_type, timestamp='synthetic-time')
                if failure == 'mismatch': rows[message_id]['session_id'] = 'different-session'
        async def persist(sid, mid, content, **kwargs):
            calls.append('persist')
            if failure == 'persist': raise RuntimeError('persist failed')
            session.messages.append({'id': mid})
        async def send(payload): calls.append('broadcast')
        module.configure_invitation(SimpleNamespace(add_message=add), persist,
            lambda: SimpleNamespace(get_current_session_id=current, get_session=get_session),
            lambda: [SimpleNamespace(send_json=send)])
        chronicle = SimpleNamespace(get_global_chronicle=lambda: SimpleNamespace(get_message_by_msg_id=rows.get))
        with patch.dict(sys.modules, {'event_chronicle': chronicle}):
            if failure:
                with self.assertRaises(RuntimeError):
                    asyncio.run(module.invite_action('synthetic invitation', 'synthetic-event', 'synthetic-action'))
                self.assertNotIn('broadcast', calls)
                if failure != 'persist': self.assertNotIn('persist', calls)
            else:
                result = asyncio.run(module.invite_action('synthetic invitation', 'synthetic-event', 'synthetic-action'))
                self.assertEqual(calls, ['write', 'persist', 'broadcast'])
                self.assertTrue(result['invitation_message_id'].startswith('ui_action_'))

    def test_invitation_durable_order(self): self._invitation()

    def test_invitation_failures_do_not_broadcast(self):
        for failure in ('write', 'missing', 'mismatch', 'persist'):
            with self.subTest(failure=failure): self._invitation(failure)


if __name__ == '__main__':
    unittest.main()
