from contextlib import closing
import json
from threading import Lock
import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, model_validator

from app import db
from app.agents import translation as agent
from app.agents.client import ModelError
from app.config import PROJECT_ROOT
from app.schemas import EvidenceBlock, Schema, utc_now
from app.services.reading_runs import safe_error
from app.services.workflow import config_digest, digest

LOCK = Lock()


class PreviewRequest(Schema):
    document_id: UUID
    evidence_id: UUID
    scope: Literal['paragraph', 'section'] = 'paragraph'
    target: Literal['zh-CN', 'en'] = 'zh-CN'
    glossary: list[agent.Term] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def unique_terms(self):
        if len({term.source for term in self.glossary}) != len(self.glossary):
            raise ValueError('术语原文不能重复')
        self.glossary.sort(key=lambda value: value.source)
        return self


class TranslateRequest(PreviewRequest):
    confirmation_token: str = Field(pattern=r'^[0-9a-f]{64}$')


def prepare(settings, request: PreviewRequest):
    with db.transaction(settings.db_path) as conn:
        doc = conn.execute('SELECT * FROM documents WHERE id=?', (str(request.document_id),)).fetchone()
        if doc is None:
            raise LookupError('文档不存在')
        row = conn.execute('SELECT * FROM evidence_blocks WHERE id=? AND document_id=?', (str(request.evidence_id), str(request.document_id))).fetchone()
        if row is None:
            raise LookupError('段落证据不存在或不属于此文件')
        rows = [row]
        if request.scope == 'section':
            if not row['section']:
                raise ValueError('此段没有章节候选，不能判定当前章节；请选择单段')
            page = conn.execute('SELECT * FROM evidence_blocks WHERE document_id=? AND physical_page=? ORDER BY paragraph_index',
                                (str(request.document_id), row['physical_page'])).fetchall()
            index = next(i for i, value in enumerate(page) if value['id'] == row['id'])
            start = end = index
            while start > 0 and page[start-1]['section'] == row['section']: start -= 1
            while end+1 < len(page) and page[end+1]['section'] == row['section']: end += 1
            rows = page[start:end+1]
        if len(rows) > 6 or sum(len(value['text']) for value in rows) > 4000:
            raise ValueError('本次范围超过6块／4000字符上限，未发送；请改选单段，不自动截断章节')
        blocks = []
        for value in rows:
            values = dict(value)
            values['bbox'] = tuple(values.pop(key) for key in ('x0','top','x1','bottom'))
            blocks.append(EvidenceBlock.model_validate(values))
    protected = [agent.protect(block, request.glossary) for block in blocks]
    config = config_digest(settings)
    prompt_hash = digest((PROJECT_ROOT/f'prompts/{agent.PROMPT_VERSION}.md').read_text(encoding='utf-8'))
    keys = {value.block.id: digest({'evidence': value.block.model_dump(mode='json'), 'sha256': doc['sha256'], 'parse': doc['parse_version'],
            'config': config, 'prompt': prompt_hash, 'protection': agent.PROTECTION_VERSION,
            'target': request.target, 'glossary': [term.model_dump() for term in request.glossary]}) for value in protected}
    missing, errors = settings.model_status()
    url = urlsplit(settings.model_base_url)
    if url.query or url.fragment:
        errors = [*errors, '模型基址不能包含查询参数或片段']
    service = f'{url.scheme}://{url.netloc}' if not missing and not errors else None
    token = digest({'request': request.model_dump(mode='json', exclude={'confirmation_token'}), 'keys': list(keys.values())})
    return protected, keys, token, {'configured': not missing and not errors, 'missing': missing, 'errors': errors,
                                 'service': service, 'model': settings.model_name or None}


def cached(settings, key: str):
    path = settings.data_dir/'translations'/f'{key}.json'
    root = settings.data_dir.resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError('翻译缓存路径超出数据目录')
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError('翻译缓存JSON损坏，未重新调用模型') from exc
    if not isinstance(value, dict):
        raise ValueError('翻译缓存格式损坏，未重新调用模型')
    checksum = value.pop('checksum', None)
    if value.get('cache_key') != key or checksum != digest(value):
        raise ValueError('翻译缓存完整性检查失败，未重新调用模型')
    return value


def preview(settings, request: PreviewRequest):
    values, keys, token, configuration = prepare(settings, request)
    entries = []
    for value in values:
        previous = cached(settings, keys[value.block.id])
        entries.append({'evidence_id': str(value.block.id), 'physical_page': value.block.physical_page,
            'section': value.block.section, 'text': value.block.text, 'cached': previous is not None,
            'will_send': previous is None and value.local_status is None,
            'preservation': value.local_status, 'protected_count': len(value.tokens)})
    return {'confirmation_token': token, 'configuration': configuration, 'scope': request.scope, 'target': request.target,
            'glossary': [term.model_dump() for term in request.glossary], 'blocks': entries,
            'warning': '章节仅指当前物理页中同一章节候选的连续块，不保证完整章节。确认覆盖下列范围；缓存命中和本地保留的块不发送。'
                       '发送内容为保护后的段落文本、证据ID、目标语言、术语表和翻译指令，不上传PDF文件。'}


def translate(settings, request: TranslateRequest):
    if not LOCK.acquire(blocking=False):
        raise ValueError('已有翻译正在执行，请完成后重试；原文阅读不受影响')
    started = time.monotonic()
    reply = None
    try:
        values, keys, token, configuration = prepare(settings, request)
        if token != request.confirmation_token:
            raise ValueError('原文或翻译配置已变化，请重新预览并确认；未发送')
        found = {value.block.id: cached(settings, keys[value.block.id]) for value in values}
        pending = [value for value in values if found[value.block.id] is None and value.local_status is None]
        if pending and not configuration['configured']:
            raise ModelError('模型配置缺失或无效：'+'、'.join(configuration['missing']+configuration['errors']))
        translated = {}
        if pending:
            reply = agent.translate(settings, pending, request.target, request.glossary)
            try:
                translated = agent.validate(reply.content, pending)
            except ValueError as exc:
                raise ModelError('翻译结构或保护片段校验失败，未保存本次模型输出', reply.calls) from exc
        results = []
        for value in values:
            block, key = value.block, keys[value.block.id]
            previous = found[block.id]
            if previous is None:
                text = translated[block.id] if value.local_status is None else block.text if value.local_status == 'preserved' else value.restore(value.masked)
                previous = {'cache_key': key, 'evidence_id': str(block.id), 'document_id': str(block.document_id), 'physical_page': block.physical_page,
                    'source': block.text, 'translation': text, 'status': value.local_status or 'translated', 'target': request.target,
                    'model': configuration['model'], 'service': configuration['service'], 'prompt_version': agent.PROMPT_VERSION, 'protection_version': agent.PROTECTION_VERSION,
                    'terms': value.terms, 'created_at': utc_now().isoformat(), 'protected_count': len(value.tokens)}
                directory = settings.data_dir/'translations'
                directory.mkdir(parents=True, exist_ok=True)
                path = directory/f'{key}.json'
                temporary = path.with_suffix('.tmp')
                try:
                    temporary.write_text(json.dumps({**previous, 'checksum': digest(previous)}, ensure_ascii=False), encoding='utf-8')
                    temporary.replace(path)
                except OSError:
                    temporary.unlink(missing_ok=True)
                    raise
            results.append({**previous, 'cached': found[block.id] is not None})
        return {'results': results, 'api_calls': reply.calls if reply else 0, 'usage': reply.usage if reply else None,
                'elapsed_ms': round((time.monotonic()-started)*1000), 'target': request.target,
                'warning': '机器译文需逐段核对，不保证语义准确。本地保留块未做模型翻译；不覆盖PDF、证据或笔记。'
                           '用量仅为服务已返回部分，失败／重试可能缺失，费用未知；缓存命中不重复计历史用量。'}
    except ModelError as exc:
        usage = json.dumps(reply.usage, ensure_ascii=False) if reply and reply.usage else '服务未报告或失败期间未知'
        raise ModelError(f'{safe_error(settings, exc)}；已知请求{exc.calls}次；已返回用量：{usage}（重试／失败用量可能缺失）', exc.calls) from None
    finally:
        LOCK.release()
