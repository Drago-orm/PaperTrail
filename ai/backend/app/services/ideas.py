"""Append-only Idea revisions and immutable, supported note/evidence origins."""
from contextlib import closing
import json
from uuid import UUID, uuid4

from app import db
from app.agents.ideas import IdeaDraft, SourceChoice
from app.config import Settings
from app.schemas import Idea
from app.services.reading_runs import note_from_row
from app.services.retrieval import context


def from_row(row) -> Idea:
    values = dict(row)
    if values['root_id'] is None:
        raise ValueError('Idea根版本信息缺失')
    values['novelty_checks'] = json.loads(values['novelty_checks'])
    experiment = values.pop('experiment_json')
    values['experiment'] = json.loads(experiment) if experiment is not None else None
    return Idea.model_validate(values)


def fact(conn, choice: SourceChoice) -> dict:
    row = conn.execute('SELECT * FROM notes WHERE id=?', (str(choice.note_id),)).fetchone()
    if row is None:
        raise ValueError('来源笔记版本不存在')
    note = note_from_row(row)
    item = next((item for item in note.items + note.writing_items if item.id == choice.item_id), None)
    if item is None or item.verification_status != 'supported' or note.verification_run_id is None:
        raise ValueError('Idea来源必须是有核查记录的已支持条目')
    if item.content_type not in {'author_statement','system_analysis'}:
        raise ValueError('不能把研究设想作为来源事实')
    run = conn.execute('SELECT * FROM runs WHERE id=?', (str(note.verification_run_id),)).fetchone()
    if run is None or run['status'] != 'succeeded' or run['document_id'] != str(note.document_id):
        raise ValueError('来源核查任务缺失、失败或文件版本不匹配')
    result = json.loads(run['output'] or '{}')
    verified = conn.execute('SELECT * FROM notes WHERE id=?', (result.get('note_id'),)).fetchone()
    snapshot = note_from_row(verified) if verified else None
    original = next((value for value in snapshot.items + snapshot.writing_items if value.id == item.id), None) if snapshot else None
    if original is None or original.model_dump() != item.model_dump():
        raise ValueError('来源条目与已核查快照不一致，请先重新核查')
    doc = conn.execute('''SELECT d.*,p.title,p.removed FROM documents d JOIN papers p ON p.id=d.paper_id WHERE d.id=?''', (str(note.document_id),)).fetchone()
    if doc is None:
        raise ValueError('来源文件版本缺失')
    if doc['parse_version'] != run['parse_version']:
        raise ValueError('来源解析版本已变化，请先重新核查')
    latest = conn.execute('SELECT id FROM notes WHERE document_id=? ORDER BY version DESC LIMIT 1', (str(note.document_id),)).fetchone()
    return {'note_id': str(note.id), 'item_id': str(item.id), 'paper_id': str(note.paper_id), 'document_id': str(note.document_id),
            'paper_title': doc['title'], 'removed': bool(doc['removed']), 'file_version': doc['version'],
            'sha256': doc['sha256'], 'parse_version': doc['parse_version'], 'note_version': note.version,
            'latest_note_id': latest['id'], 'verification_run_id': str(note.verification_run_id),
            'field': item.field, 'content_type': item.content_type, 'content': item.content, 'source_mode': note.source_mode,
            'evidence_ids': [str(key) for key in item.evidence_ids], 'quotes': [quote.model_dump(mode='json') for quote in item.quotes]}


def available(settings: Settings) -> list[dict]:
    result = []
    with closing(db.connect(settings.db_path)) as conn:
        rows = conn.execute('''SELECT n.* FROM notes n JOIN papers p ON p.id=n.paper_id
            WHERE p.removed=0 AND n.version=(SELECT MAX(v.version) FROM notes v WHERE v.document_id=n.document_id)''').fetchall()
        for row in rows:
            note = note_from_row(row)
            for item in note.items + note.writing_items:
                if item.verification_status == 'supported' and note.verification_run_id:
                    result.append(fact(conn, SourceChoice(note_id=note.id, item_id=item.id)))
    return result


def inputs(settings: Settings, choices: list[SourceChoice]) -> tuple[list[dict], list[dict], list[str]]:
    pairs = [(choice.note_id, choice.item_id) for choice in choices]
    if not 1 <= len(pairs) <= 6 or len(set(pairs)) != len(pairs):
        raise ValueError('请选择1—6个不重复的已支持来源')
    with closing(db.connect(settings.db_path)) as conn:
        facts = sorted((fact(conn, choice) for choice in choices),key=lambda value:(value['note_id'],value['item_id']))
        documents = {value['document_id'] for value in facts}
        if len(documents) > 3 or sum(len(value['content']) for value in facts) > 12000:
            raise ValueError('Idea来源限3个文件、12000字符的条目内容')
        note_ids = [value['note_id'] for value in facts]
        marks = ','.join('?' for _ in note_ids)
        edits = [row['id'] for row in conn.execute(f'''SELECT DISTINCT m.id FROM ideas m WHERE m.is_user_edited=1
            AND m.root_id IN (SELECT i.root_id FROM ideas i JOIN idea_sources s ON s.idea_id=i.id
                WHERE s.note_id IN ({marks})) ORDER BY m.id''', note_ids)]
    blocks = {}
    anchors = set()
    for document_id in sorted(documents):
        keys = [UUID(key) for value in facts if value['document_id'] == document_id for key in value['evidence_ids']]
        anchors.update(str(key) for key in keys)
        for block in context(settings.db_path, UUID(document_id), keys):
            blocks[str(block.id)] = block
    selected = [block for key, block in blocks.items() if key in anchors]
    remaining = 16000-sum(len(block.text) for block in selected)
    if len(selected) > 40 or remaining < 0:
        raise ValueError('来源原文超过40块/16000字符，请减少来源')
    for key, block in blocks.items():
        if key in anchors or len(selected) == 40 or remaining <= 0:
            continue
        text = block.text[:remaining]
        selected.append(block.model_copy(update={'text': text}))
        remaining -= len(text)
    originals = {str(block.id): block.text for block in selected}
    for value in facts:
        if not value['quotes'] or any(quote['evidence_id'] not in originals or quote['text'] not in originals[quote['evidence_id']] for quote in value['quotes']):
            raise ValueError('已核查来源的原文片段缺失或不匹配')
    evidence = [{'id': str(block.id), 'document_id': str(block.document_id), 'physical_page': block.physical_page, 'text': block.text} for block in selected]
    return facts, evidence, edits


def validate_sources(draft: IdeaDraft, allowed: list[dict]):
    expected = {(value['note_id'], value['item_id']) for value in allowed}
    actual = [(str(value.note_id), str(value.item_id)) for value in draft.sources]
    if len(set(actual)) != len(actual) or not set(actual) <= expected:
        raise ValueError('Idea来源必须来自本次提供的已核查条目，且不能重复')
    for name in ('question','hypothesis','derivation','difference','resources','failure_conditions'):
        if not getattr(draft, name).strip():
            raise ValueError('Idea研究问题、推导和验证计划不能留白')
    if any(not value.strip() or len(value)>600 for value in draft.novelty_checks):
        raise ValueError('新颖性待核实问题须为1—600字')


def insert(conn, draft: IdeaDraft, facts: list[dict], *, base: Idea | None = None, status='draft', run_id=None) -> Idea:
    validate_sources(draft, facts)
    idea = Idea(**draft.model_dump(exclude={'sources'}), root_id=base.root_id if base else None,
        parent_idea_id=base.id if base else None, version=base.version+1 if base else 1,
        is_user_edited=run_id is None, generation_run_id=base.generation_run_id if base else run_id, status=status)
    conn.execute('''INSERT INTO ideas(id,question,hypothesis,derivation,difference,minimum_experiment,resources,
        failure_conditions,novelty_checks,status,created_at,root_id,version,parent_idea_id,is_user_edited,generation_run_id,experiment_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (str(idea.id),idea.question,idea.hypothesis,idea.derivation,idea.difference,
        idea.minimum_experiment,idea.resources,idea.failure_conditions,json.dumps(idea.novelty_checks),idea.status,
        idea.created_at.isoformat(),str(idea.root_id),idea.version,str(idea.parent_idea_id) if idea.parent_idea_id else None,
        int(idea.is_user_edited),str(idea.generation_run_id) if idea.generation_run_id else None,idea.experiment.model_dump_json()))
    chosen = {(str(choice.note_id), str(choice.item_id)) for choice in draft.sources}
    for value in facts:
        if (value['note_id'],value['item_id']) not in chosen:
            continue
        for key in value['evidence_ids']:
            conn.execute('''INSERT INTO idea_sources(id,idea_id,paper_id,document_id,evidence_id,note_id,item_id)
                VALUES (?,?,?,?,?,?,?)''', (str(uuid4()),str(idea.id),value['paper_id'],value['document_id'],key,value['note_id'],value['item_id']))
    return idea


def save(settings: Settings, draft: IdeaDraft, base_id: UUID | None, status: str) -> Idea:
    facts, _, _ = inputs(settings, draft.sources)
    with db.transaction(settings.db_path) as conn:
        base = None
        if base_id:
            row = conn.execute('SELECT * FROM ideas WHERE id=?', (str(base_id),)).fetchone()
            if row is None:
                raise LookupError('Idea版本不存在')
            base = from_row(row)
            head = conn.execute('SELECT id FROM ideas WHERE root_id=? ORDER BY version DESC LIMIT 1', (str(base.root_id),)).fetchone()
            if head['id'] != str(base.id):
                raise ValueError('Idea已有新版本，未覆盖当前编辑')
        return insert(conn, draft, facts, base=base, status=status)


def view(conn, row) -> dict:
    result = from_row(row).model_dump(mode='json')
    sources = []
    for source in conn.execute('''SELECT s.*,p.title,p.removed,d.version AS file_version,d.sha256,e.physical_page
        FROM idea_sources s JOIN papers p ON p.id=s.paper_id JOIN documents d ON d.id=s.document_id
        LEFT JOIN evidence_blocks e ON e.id=s.evidence_id WHERE s.idea_id=?''', (row['id'],)):
        value = dict(source)
        value['paper_title'] = value.pop('title')
        value['removed'] = bool(value['removed'])
        note_row = conn.execute('SELECT * FROM notes WHERE id=?', (value['note_id'],)).fetchone()
        note = note_from_row(note_row) if note_row else None
        item = next((item for item in note.items+note.writing_items if str(item.id)==value['item_id']), None) if note else None
        value.update(note_version=note.version if note else None, source_mode=note.source_mode if note else None, field=item.field if item else None,
                     content=item.content if item else None, content_type=item.content_type if item else None,
                     warning=None if item else '旧来源未保存具体条目，无法提供条目内容')
        sources.append(value)
    result['sources'] = sources
    return result


def listing(settings: Settings, query='', status='') -> list[dict]:
    with closing(db.connect(settings.db_path)) as conn:
        rows = conn.execute('''SELECT i.* FROM ideas i WHERE version=(SELECT MAX(v.version) FROM ideas v WHERE v.root_id=i.root_id)
            AND (?='' OR i.status=?) AND (?='' OR instr(lower(i.question||' '||i.hypothesis),lower(?))>0) ORDER BY created_at DESC''',
            (status,status,query,query)).fetchall()
        return [view(conn,row) for row in rows]
