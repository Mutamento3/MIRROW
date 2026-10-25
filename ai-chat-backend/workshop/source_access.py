"""Read-only MIRROW source namespace, also usable by existing Codex threads."""
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

from production_data_mcp.code_access import CodeAccess


def capability_catalog():
    return {
        'trust': 'mirrow_source',
        'runtime_personal_data': False,
        'source_roots': ['backend', 'frontend'],
        'read': 'mirrow://backend/affect/service.py?start=922&end=953',
        'search': 'mirrow://search?q=render_resonance',
        'capabilities': [{
            'id': 'affect_resonance',
            'fact': 'MIRROW 已有 Affect v2 心情识别；识别和共振传递是两个独立开关。AI 上下文使用 render_resonance；人类伙伴面板仍可显示未传递的识别结果。',
            'sources': ['backend:affect/service.py:666-674', 'backend:affect/service.py:784-987',
                        'backend:routers/affect_router.py:136-168', 'backend:context_builder/builder.py:581-591',
                        'backend:workshop/runtime.py:1-40', 'frontend:workshop/WorkshopArtifactFrame.tsx:1-26'],
            'integration': '自动心情灯可复用 Affect。HTML 以 <meta name="mirrow-workshop-capabilities" content="mood.v1"> 选择接入；宿主加载时和每15秒定向 postMessage {type:"mirrow-workshop-mood",version:1,artifact_id,mood:{status:"available"|"unavailable",family,checked_at}}。作品验证 event.source===parent 和版本；十类 family 为愉悦、亲近、安定、兴趣、低落、担忧、恼怒、排斥、惊讶、复杂/未归类。引擎 enabled、识别开、共振开且存在有效结果才 available；其余 unavailable/family=null。不传个人原文或原因。已有验收版在作品架自动运行，返工时保留旧验收版。禁止任意联网。识别按 episode/checkpoint 更新，不是逐句实时。质检的 mood action 可注入虚构fixture再断言灯的变化。',
        }],
    }


class SourceAccess:
    def __init__(self, backend_root=None):
        backend = Path(backend_root or Path(__file__).resolve().parents[1])
        self.reader = CodeAccess(SimpleNamespace(backend_root=backend, repo_root=backend.parent))
        # Only source roots; this does not load the production-data MCP or its auth.
        self.reader.roots.pop('references', None)

    async def read(self, uri):
        parsed = urlsplit(uri)
        if parsed.scheme != 'mirrow' or parsed.fragment:
            raise ValueError('invalid_source_uri')
        query = parse_qs(parsed.query, strict_parsing=True)
        if parsed.netloc == 'capabilities' and not parsed.path and not query:
            return capability_catalog()
        if parsed.netloc == 'search' and not parsed.path and set(query) == {'q'}:
            matches = await self.reader.search(query['q'][0])
            if not matches and self.reader._rg_path:
                # Installed rg can return no rows under a different launch PATH;
                # retain the existing bounded safe in-process fallback.
                self.reader._rg_path = None
                matches = await self.reader.search(query['q'][0])
            return {'trust': 'mirrow_source', 'matches': matches}
        if parsed.netloc not in self.reader.roots or set(query) - {'start', 'end'}:
            raise ValueError('invalid_source_uri')
        start = int(query.get('start', ['1'])[0])
        end = int(query.get('end', [str(start + 199)])[0])
        return await self.reader.read(parsed.netloc, unquote(parsed.path).lstrip('/'), start, end)


async def execution_evidence():
    """Current bounded source evidence; no personal state or remote connector."""
    access = SourceAccess()
    # Locate the named source function: unrelated edits must not silently move
    # the body evidence to another function at an obsolete numeric line.
    source = access.reader._safe_file('backend', 'affect/service.py').read_text(encoding='utf-8').splitlines()
    start = next(index for index, line in enumerate(source, 1) if line.startswith('    def _render_v2_resonance('))
    end = next((index-1 for index, line in enumerate(source[start:], start+1) if line.startswith('    def ')), min(start+100, len(source)))
    uris = ('mirrow://backend/workshop/runtime.py?start=1&end=40',
            f'mirrow://backend/affect/service.py?start={start}&end={end}',
            'mirrow://frontend/workshop/WorkshopArtifactFrame.tsx?start=1&end=26')
    return {'source': 'workshop_local_readonly', 'catalog': capability_catalog(),
            'excerpts': [{'uri': uri, 'result': await access.read(uri)} for uri in uris]}
