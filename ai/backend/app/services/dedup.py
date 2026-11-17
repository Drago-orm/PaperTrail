"""Explainable candidates and metadata-only merges; original source ownership never moves."""
from contextlib import closing
from difflib import SequenceMatcher
from hashlib import sha256
import json
import re
import unicodedata
from uuid import UUID, uuid4

from app import db
from app.schemas import utc_now


def normalize_title(value: str) -> str:
    return ''.join(char for char in unicodedata.normalize('NFKC', value).casefold() if char.isalnum())


def normalize_doi(value: str) -> str:
    value = value.strip().casefold()
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)', '', value)
    # This is a shape check, not a registration lookup. Invalid or absent DOI is no signal.
    return value if re.fullmatch(r'10\.\d{4,9}/\S+', value) else ''


def describe(conn, key: str) -> dict:
    row = conn.execute('SELECT * FROM papers WHERE id=?', (key,)).fetchone()
    if row is None:
        raise LookupError('文献不存在')
    owner = conn.execute('SELECT library_id FROM paper_owners WHERE original_id=?', (key,)).fetchone()[0]
    if owner != key:
        raise ValueError(f'条目已汇总到主文献 {owner}，请刷新后比较主条目或先撤销合并')
    members = [dict(item) for item in conn.execute('''SELECT p.id,p.title,p.removed FROM papers p
        JOIN paper_owners o ON o.original_id=p.id WHERE o.library_id=? ORDER BY p.id''', (key,))]
    docs = [dict(item) for item in conn.execute('''SELECT d.id,d.paper_id,d.version,d.original_name,d.sha256,d.parse_status
        FROM documents d JOIN paper_owners o ON o.original_id=d.paper_id WHERE o.library_id=? ORDER BY d.id''', (key,))]
    counts = {}
    for table in ('notes', 'idea_sources'):
        counts[table] = conn.execute(f'''SELECT COUNT(*) FROM {table} s JOIN paper_owners o ON o.original_id=s.paper_id
            WHERE o.library_id=?''', (key,)).fetchone()[0]
    return {**db.paper_values(row), 'removed': bool(row['removed']), 'members': members, 'documents': docs, 'counts': counts}


def signals(left: dict, right: dict) -> dict:
    reasons, differences = [], []
    hashes = {doc['sha256'] for doc in left['documents']} & {doc['sha256'] for doc in right['documents']}
    if hashes:
        reasons.append('至少一个附件SHA256完全相同；只说明文件字节相同')
    a, b = normalize_doi(left['doi']), normalize_doi(right['doi'])
    if a and a == b:
        reasons.append('规范化DOI相同（未查询注册机构）')
    elif a and b:
        differences.append('DOI不同，可能是不同版本或不同研究，需核对')
    else:
        differences.append('至少一侧缺少形态有效的DOI，不能用于判断')
    title_a, title_b = normalize_title(left['title']), normalize_title(right['title'])
    ratio = SequenceMatcher(None, title_a[:512], title_b[:512], autojunk=False).ratio() if title_a and title_b else 0
    authors_a = {normalize_title(value) for value in left['authors']} - {''}
    authors_b = {normalize_title(value) for value in right['authors']} - {''}
    shared_authors = bool(authors_a & authors_b)
    nearby_year = left['year'] is not None and right['year'] is not None and abs(left['year']-right['year']) <= 1
    if title_a and title_a == title_b:
        reasons.append('NFKC／大小写／空白与标点规范化后题目相同')
    elif ratio >= .85 or ratio >= .65 and shared_authors and nearby_year:
        reasons.append(f'规范化题目前512字符相近（序列匹配系数{ratio:.2f}，不是重复概率）')
    else:
        differences.append('题目缺失或未达到规则候选阈值')
    differences.append('存在规范化后相同作者' if shared_authors else '作者缺失或未发现完整姓名匹配；不推断姓名缩写')
    if left['year'] is None or right['year'] is None:
        differences.append('年份至少一侧未提供，不推断出版年份')
    elif left['year'] != right['year']:
        differences.append(f"年份不同：{left['year']}／{right['year']}；预印本和正式版优先关联，不能据此删除")
    else:
        differences.append('填写的年份相同；不说明研究相同')
    return {'candidate': bool(reasons), 'kind': 'exact_file' if hashes else 'suspected',
            'reasons': reasons, 'differences': differences}


def comparison(conn, left_id: str, right_id: str) -> dict:
    if left_id == right_id:
        raise ValueError('请选择不同的两个文献条目')
    left, right = describe(conn, left_id), describe(conn, right_id)
    links = [dict(row) for row in conn.execute('''SELECT r.* FROM paper_relations r
        JOIN paper_owners a ON a.original_id=r.left_id JOIN paper_owners b ON b.original_id=r.right_id
        WHERE (a.library_id=? AND b.library_id=?) OR (a.library_id=? AND b.library_id=?) ORDER BY r.left_id,r.right_id''',
        (left_id, right_id, right_id, left_id))]
    pair = sorted((left_id, right_id))
    relation = next((link['kind'] for link in links if [link['left_id'], link['right_id']] == pair), None)
    value = {'left': left, 'right': right, 'links': links, 'relation': relation, **signals(left, right)}
    value['token'] = sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return value


def compare(settings, left_id: UUID, right_id: UUID) -> dict:
    with db.transaction(settings.db_path) as conn:
        return comparison(conn, str(left_id), str(right_id))


def candidates(settings, paper_id: UUID, include_distinct=False) -> list[dict]:
    key = str(paper_id)
    with db.transaction(settings.db_path) as conn:
        left = describe(conn, key)
        result = []
        for row in conn.execute('''SELECT p.id FROM papers p JOIN paper_owners o ON o.original_id=p.id
            WHERE o.library_id=p.id AND p.id<>? AND p.removed=0 ORDER BY p.id''', (key,)).fetchall():
            right = describe(conn, row['id'])
            signal = signals(left, right)
            # Relations from absorbed original entries remain readable under their current group.
            linked = conn.execute('''SELECT 1 FROM paper_relations r
                JOIN paper_owners a ON a.original_id=r.left_id JOIN paper_owners b ON b.original_id=r.right_id
                WHERE (a.library_id=? AND b.library_id=?) OR (a.library_id=? AND b.library_id=?)''',
                (key, row['id'], row['id'], key)).fetchone()
            if not signal['candidate'] and not linked:
                continue
            value = comparison(conn, key, row['id'])
            if value['relation'] == 'distinct' and not include_distinct:
                continue
            result.append({'id': row['id'], 'title': right['title'], 'relation': value['relation'],
                           'links': value['links'], **signal})
        return result


def assert_current(conn, left_id: UUID, right_id: UUID, token: str) -> dict:
    value = comparison(conn, str(left_id), str(right_id))
    if value['token'] != token:
        raise ValueError('比较后文献、附件、来源或关系已变化，请重新比较；未覆盖任何内容')
    if value['left']['removed'] or value['right']['removed']:
        raise ValueError('请先恢复移除的文献，再确认关系或合并')
    return value


def set_relation(settings, left_id: UUID, right_id: UUID, token: str, kind: str | None):
    with db.transaction(settings.db_path) as conn:
        assert_current(conn, left_id, right_id, token)
        pair = sorted((str(left_id), str(right_id)))
        if kind is None:
            conn.execute('DELETE FROM paper_relations WHERE left_id=? AND right_id=?', pair)
        else:
            conn.execute('''INSERT INTO paper_relations(left_id,right_id,kind,created_at) VALUES (?,?,?,?)
                ON CONFLICT(left_id,right_id) DO UPDATE SET kind=excluded.kind,created_at=excluded.created_at''',
                (*pair, kind, utc_now().isoformat()))
    return {'relation': kind}


def merge(settings, left_id: UUID, right_id: UUID, token: str, fields: dict[str, str]) -> dict:
    if set(fields) != set(db.PAPER_EDITABLE) or any(side not in {'left', 'right'} for side in fields.values()):
        raise ValueError('请为每个元数据字段选择左侧或右侧值')
    with db.transaction(settings.db_path) as conn:
        before = assert_current(conn, left_id, right_id, token)
        if len(before['right']['members']) != 1:
            raise ValueError('右侧已汇总其他条目，请先撤销其合并；当前支持向一个主条目逐个合并独立条目')
        if before['relation'] == 'distinct':
            raise ValueError('该对已标记非重复，请先清除关系再重新比较')
        after = {name: before[side][name] for name, side in fields.items() if before[side][name] != before['left'][name]}
        old = {name: before['left'][name] for name in after}
        if after:
            db.update_metadata(conn, str(left_id), after)
        # Audit original relationships; undo never rewrites these rows, including later additions.
        ids = [member['id'] for side in ('left', 'right') for member in before[side]['members']]
        marks = ','.join('?' for _ in ids)
        before['origins'] = {table: [dict(row) for row in conn.execute(f'SELECT {columns} FROM {table} WHERE paper_id IN ({marks}) ORDER BY id', ids)]
                             for table, columns in [('notes', 'id,paper_id,document_id,parent_note_id'),
                                                    ('idea_sources', 'id,idea_id,paper_id,document_id,note_id,item_id,evidence_id')]}
        key, created = str(uuid4()), utc_now().isoformat()
        changes = {'metadata_before': old, 'metadata_after': after, 'fields': fields}
        conn.execute('''INSERT INTO merge_events(id,primary_paper_id,secondary_paper_id,before_snapshot,changes,status,created_at)
            VALUES (?,?,?,?,?,'applied',?)''', (key, str(left_id), str(right_id), json.dumps(before, ensure_ascii=False), json.dumps(changes, ensure_ascii=False), created))
    return {'id': key, 'status': 'applied', 'primary_paper_id': str(left_id), 'secondary_paper_id': str(right_id)}


def undo_problem(conn, row) -> str | None:
    if row['status'] != 'applied':
        return '该合并已经撤销'
    if conn.execute("SELECT 1 FROM merge_events WHERE primary_paper_id=? AND status='applied' AND rowid>?", (row['primary_paper_id'], row['event_order'])).fetchone():
        return '主条目还有较新的合并，请先按倒序撤销'
    current = db.paper_values(conn.execute('SELECT * FROM papers WHERE id=?', (row['primary_paper_id'],)).fetchone())
    changes = json.loads(row['changes'])
    conflicts = [name for name, value in changes['metadata_after'].items() if current[name] != value]
    if conflicts:
        return '合并后这些字段已修改，不能覆盖：' + '、'.join(conflicts)
    return None


def history(settings, paper_id: UUID) -> list[dict]:
    with closing(db.connect(settings.db_path)) as conn:
        if conn.execute('SELECT 1 FROM papers WHERE id=?', (str(paper_id),)).fetchone() is None:
            raise LookupError('文献不存在')
        rows = conn.execute('''SELECT *,rowid AS event_order FROM merge_events WHERE primary_paper_id=? OR secondary_paper_id=?
            ORDER BY rowid DESC''', (str(paper_id), str(paper_id))).fetchall()
        result = []
        for row in rows:
            before = json.loads(row['before_snapshot'])
            # Old audit rows cannot be safely inverted by this implementation.
            supported = 'left' in before and 'metadata_after' in json.loads(row['changes'])
            result.append({'id': row['id'], 'status': row['status'], 'primary_paper_id': row['primary_paper_id'],
                'secondary_paper_id': row['secondary_paper_id'], 'primary_title': before.get('left', {}).get('title', '旧记录未提供'),
                'secondary_title': before.get('right', {}).get('title', '旧记录未提供'), 'created_at': row['created_at'],
                'reverted_at': row['reverted_at'], 'undo_conflict': undo_problem(conn, row) if supported else '旧合并记录缺少可安全撤销的字段快照'})
        return result


def undo(settings, event_id: UUID) -> dict:
    with db.transaction(settings.db_path) as conn:
        row = conn.execute('SELECT *,rowid AS event_order FROM merge_events WHERE id=?', (str(event_id),)).fetchone()
        if row is None:
            raise LookupError('合并记录不存在')
        changes = json.loads(row['changes'])
        if 'metadata_before' not in changes or 'metadata_after' not in changes:
            raise ValueError('旧合并记录缺少可安全撤销的字段快照')
        problem = undo_problem(conn, row)
        if problem:
            raise ValueError(problem)
        if changes['metadata_before']:
            db.update_metadata(conn, row['primary_paper_id'], changes['metadata_before'])
        conn.execute("UPDATE merge_events SET status='reverted',reverted_at=? WHERE id=?", (utc_now().isoformat(), str(event_id)))
    return {'id': str(event_id), 'status': 'reverted'}
