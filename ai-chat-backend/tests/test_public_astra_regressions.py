"""Release regressions: no server, device, model or private storage access."""
import ast
import asyncio
import logging
import logging.handlers
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from local_access_boundary import LocalAccessBoundary, allowed_browser_origins
from network_access import NetworkAccessPolicy

ROOT = Path(__file__).resolve().parents[1]
WS_PATHS = ('/ws/behavior', '/ws/voice', '/ws/music', '/ws/wander', '/api/calendar/ws', '/api/tasks/ws')


class AstraRegressions(unittest.TestCase):
    def test_main_dependency_declarations(self):
        text = (ROOT / 'requirements.txt').read_text(encoding='utf-8').lower()
        for package in ('python-frontmatter', 'openai', 'psutil', 'pywin32', 'pyyaml', 'requests',
                        'beautifulsoup4', 'soundfile', 'scipy', 'librosa', 'matplotlib'):
            self.assertTrue(any(line.startswith(package + '>=') for line in text.splitlines()), package)
        self.assertIn('pywin32>=306; sys_platform == "win32"', text)

    def test_unconfigured_rvc_does_not_launch_private_adapter(self):
        tree = ast.parse((ROOT / 'voice_manager/tts_pipeline.py').read_text(encoding='utf-8'))
        selected = [n for n in tree.body if
            (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith('_RVC_') for t in n.targets)) or
            (isinstance(n, ast.AsyncFunctionDef) and n.name == '_run_rvc')]
        namespace = dict(os=os, logger=SimpleNamespace(warning=lambda *args: None))
        with patch.dict(os.environ, {}, clear=True):
            exec(compile(ast.Module(body=selected, type_ignores=[]), '<rvc>', 'exec'), namespace)
            self.assertEqual(namespace['_RVC_PYTHON'], '')
            self.assertEqual(namespace['_RVC_INFER'], '')
            self.assertFalse(asyncio.run(namespace['_run_rvc']('synthetic-input', 'synthetic-output', 0)))
        functions = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertIn('text_to_voice', functions)
        self.assertIn('text_to_voice_sync', functions)

    def _request(self, kind, peer, origins, path='/api/settings'):
        events, reached = [], []
        async def app(scope, receive, send):
            reached.append(scope['path'])
            if kind == 'websocket': await send({'type': 'websocket.accept'})
        async def receive(): return {'type': 'websocket.connect'}
        async def send(event): events.append(event)
        boundary = LocalAccessBoundary(app, NetworkAccessPolicy(frozenset({'192.0.2.20'})),
                                       {'http://localhost:5173', 'capacitor://localhost'})
        asyncio.run(boundary({'type': kind, 'client': (peer, 1234), 'path': path,
            'headers': [(b'origin', value.encode()) for value in origins]}, receive, send))
        return events, reached

    def test_all_websockets_reject_untrusted_peers_and_origins_before_accept(self):
        for path in WS_PATHS:
            for peer, origins in (('192.0.2.21', []), ('192.0.2.21', ['http://localhost:5173']),
                ('127.0.0.1', ['https://untrusted.example']), ('127.0.0.1', ['null']),
                ('127.0.0.1', ['http://localhost:5173', 'https://untrusted.example'])):
                with self.subTest(path=path, peer=peer, origins=origins):
                    events, reached = self._request('websocket', peer, origins, path)
                    self.assertEqual(reached, [])
                    self.assertEqual(events, [{'type': 'websocket.close', 'code': 1008}])

    def test_local_and_registered_clients_remain_available(self):
        for peer in ('127.0.0.1', '::1', '192.0.2.20'):
            for origins in ([], ['http://localhost:5173'], ['capacitor://localhost']):
                events, reached = self._request('websocket', peer, origins, '/api/tasks/ws')
                self.assertEqual(reached, ['/api/tasks/ws'])
                self.assertEqual(events, [{'type': 'websocket.accept'}])

    def test_http_origin_denial_is_actual_admission_not_only_cors(self):
        for peer, origins in (('127.0.0.1', ['https://untrusted.example']), ('192.0.2.21', [])):
            events, reached = self._request('http', peer, origins)
            self.assertEqual(reached, [])
            self.assertEqual(events[0]['status'], 403)

    def test_global_boundary_registered_with_same_cors_origins(self):
        tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == 'add_middleware' and n.args and isinstance(n.args[0], ast.Name)]
        boundary = next(c for c in calls if c.args[0].id == 'LocalAccessBoundary')
        cors = next(c for c in calls if c.args[0].id == 'CORSMiddleware')
        self.assertEqual(next(k.value.id for k in boundary.keywords if k.arg == 'network_policy'), '_NETWORK_ACCESS')
        self.assertEqual(next(k.value.id for k in boundary.keywords if k.arg == 'allowed_origins'), 'allowed_origins')
        self.assertEqual(next(k.value.id for k in cors.keywords if k.arg == 'allow_origins'), 'allowed_origins')

    def test_explicit_origin_configuration_does_not_enable_wildcards(self):
        with patch.dict(os.environ, {'ALLOWED_ORIGINS': '*,null,https://ui.example', 'CORS_ALLOW_ALL': 'true'}, clear=True):
            origins = allowed_browser_origins()
        self.assertIn('https://ui.example', origins)
        self.assertIn('http://localhost:5173', origins)
        self.assertNotIn('*', origins)
        self.assertNotIn('null', origins)

    def test_first_start_creates_log_directory_under_backend(self):
        tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
        selected = [n for n in tree.body if
            (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_log_dir' for t in n.targets)) or
            (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and
             (ast.unparse(n.value.func) == 'logging.basicConfig' or
              (ast.unparse(n.value.func) == 'os.makedirs' and ast.unparse(n.value.args[0]) == '_log_dir')))]
        self.assertEqual(len(selected), 3)
        captured = []
        fake_logging = SimpleNamespace(INFO=logging.INFO, handlers=logging.handlers,
            StreamHandler=logging.StreamHandler, basicConfig=lambda **kw: captured.extend(kw['handlers']))
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory) / 'backend'
            backend.mkdir()
            try:
                exec(compile(ast.Module(body=selected, type_ignores=[]), '<startup>', 'exec'),
                     {'os': os, '__file__': str(backend / 'main.py'), 'logging': fake_logging})
                self.assertTrue((backend / 'logs/mirrow.log').is_file())
            finally:
                for handler in captured: handler.close()

    def test_default_frontend_backend_ports_match(self):
        tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'os.getenv'
                 and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == 'PORT']
        self.assertEqual(calls[0].args[1].value, 8005)
        self.assertIn("VITE_BACKEND_PORT || '8005'", (ROOT.parent / 'ai-chat-frontend/src/config.ts').read_text(encoding='utf-8'))
        self.assertIn('VITE_BACKEND_PORT=8005', (ROOT.parent / 'ai-chat-frontend/.env.example').read_text(encoding='utf-8'))


if __name__ == '__main__': unittest.main()
