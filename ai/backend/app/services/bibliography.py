"""Local metadata exchange; source text is data and never executed or fetched."""
from hashlib import sha256
import re
from uuid import UUID

import bibtexparser
from bibtexparser.middlewares.names import split_multiple_persons_names
from bibtexparser.model import Entry, Field, ImplicitComment
import rispy

from app import db
from app.schemas import Paper
from app.services.dedup import signals
from app.services.workflow import digest

MAX_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 100
BIB_IDENTIFIERS = {'url': 'url', 'isbn': 'isbn', 'issn': 'issn', 'eprint': 'eprint',
                   'accessionnumber': 'accession_number', 'serialnumber': 'serial_number'}
RIS_LISTS = list(rispy.TAG_KEY_MAPPING)
NOTICE = ['仅交换元数据；不创建附件、证据或笔记，不获取链接或操作本机Zotero。',
          '题目、作者、摘要保留字面文字；BibTeX的LaTeX命令不解码、不渲染、不执行。',
          '不保存条目类型、引用键和未映射字段；导出使用通用类型及PaperTrail ID，不提供排版引用样式。']


class PlainRisWriter(rispy.RisWriter):
    def set_header(self, count):
        return None


def ungroup(name):
    # A whole-name brace group marks a corporate author, not an additional author.
    if name.startswith('{') and name.endswith('}'):
        depth = 0
        for index, char in enumerate(name):
            if char == '{': depth += 1
            elif char == '}': depth -= 1
            if depth == 0 and index < len(name) - 1: return name
        if depth == 0: return name[1:-1]
    return name


def parse(content: bytes, format: str):
    if not content or len(content) > MAX_BYTES:
        raise ValueError('请选择非空文件，最大2 MiB')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('文件必须是UTF-8／UTF-8 BOM编码，请转换编码后重试') from exc
    if any(ord(char) < 32 and char not in '\n\r\t' for char in text):
        raise ValueError('文件含二进制或控制字符，不是支持的元数据文本')
    warnings = list(NOTICE)
    entries = []
    if format == 'bib':
        library = bibtexparser.parse_string(text)
        if library.failed_blocks:
            lines = [str(block.start_line) for block in library.failed_blocks[:8]]
            raise ValueError('BibTeX解析失败（含重复引用键／字段或未解析块），起始行：' + ', '.join(lines))
        if any(isinstance(block, ImplicitComment) and '@' in block.comment for block in library.comments):
            raise ValueError('BibTeX含未识别的条目片段，请检查语法；未导入')
        if library.comments or library.preambles or library.strings:
            warnings.append('注释／preamble不保存；string只用于当前文件的字段展开。')
        for entry in library.entries:
            fields = {f.key.lower(): f.value for f in entry.fields}
            if len(fields) != len(entry.fields):
                raise ValueError('BibTeX含大小写规范化后重复的字段，请核对后重试')
            if any(f.enclosing == 'no-enclosing' and not re.fullmatch(r'\d+', f.value) for f in entry.fields):
                raise ValueError('BibTeX含未定义字符串或不支持的拼接表达式，请展开为引号／花括号文本后重试')
            data = {key: fields.get(key, '') for key in ('title', 'abstract', 'doi')}
            data['authors'] = [ungroup(name.strip()) for name in split_multiple_persons_names(fields.get('author', '')) if name.strip()]
            data['identifiers'] = {target: [fields[key]] for key, target in BIB_IDENTIFIERS.items() if fields.get(key)}
            data['keywords'] = []
            year = fields.get('year', '')
            known = {'title', 'abstract', 'doi', 'author', 'year', *BIB_IDENTIFIERS}
            unmapped = {key: value for key, value in fields.items() if key not in known}
            entries.append((data, year, unmapped))
    else:
        # rispy deliberately tolerates incomplete records and duplicate scalar tags.
        # Validate framing first, then retain repeated fields as lists so none silently disappear.
        active, count, last_tag = False, 0, None
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip(): continue
            match = re.fullmatch(r'([A-Z][A-Z0-9])  -(?: (.*))?', line)
            if not match:
                if active and last_tag and line[:1].isspace(): continue
                raise ValueError(f'RIS第{number}行格式无效，须为双字符标签、两个空格、短横线')
            tag, value = match.groups()
            if tag == 'TY':
                if active or not value: raise ValueError(f'RIS第{number}行类型／记录边界无效')
                active = True
            elif tag == 'ER':
                if not active or value: raise ValueError(f'RIS第{number}行记录结束标记无效')
                active = False; count += 1
            elif not active:
                raise ValueError(f'RIS第{number}行在记录外，未导入')
            last_tag = tag
        if active: raise ValueError('RIS缺少ER结束标记；未导入任何条目')
        try:
            records = rispy.loads(text.replace('\r\n', '\n').replace('\r', '\n'), list_tags=RIS_LISTS)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError('RIS字段解析失败；未导入任何条目') from exc
        if len(records) != count: raise ValueError('RIS记录数量与边界不一致；未导入')
        warnings.append('RIS续行按解析器规则合为空格；AN保留为登录号，SN不推断ISBN或ISSN类型。')
        for record in records:
            def single(*keys):
                values = [value for key in keys for value in record.get(key, []) if value]
                if len(set(values)) > 1: raise ValueError(f'RIS同一标量字段重复或别名冲突：{keys}')
                return values[0] if values else ''
            data = {'title': single('title', 'primary_title'), 'abstract': single('abstract', 'notes_abstract'), 'doi': single('doi'),
                    'authors': record.get('authors', record.get('first_authors', [])), 'keywords': record.get('keywords', []),
                    'identifiers': {key: record[source] for source, key in [('urls', 'url'), ('issn', 'serial_number'), ('accession_number', 'accession_number')] if record.get(source)}}
            if record.get('authors') and record.get('first_authors') and record['authors'] != record['first_authors']:
                raise ValueError('RIS AU与A1作者列表冲突，请明确保留一组后重试')
            years = [value for key in ('year', 'publication_year') for value in record.get(key, []) if value]
            if len({value[:4] for value in years}) > 1:
                raise ValueError('RIS PY与Y1年份冲突，请核对后重试')
            year = next((value for value in years if '/' in value), years[0] if years else '')
            if any(not re.fullmatch(r'[1-9]\d{3}(?:/\d{0,2}(?:/\d{0,2})?)?', value) for value in years):
                raise ValueError('RIS年份无法映射，请改为四位年份或YYYY/MM/DD')
            known = {'type_of_reference', 'title', 'primary_title', 'abstract', 'notes_abstract', 'doi', 'authors', 'first_authors', 'keywords', 'urls', 'issn', 'accession_number', 'year', 'publication_year'}
            entries.append((data, year, {k: v for k, v in record.items() if k not in known}))
    if not 1 <= len(entries) <= MAX_ENTRIES: raise ValueError('每次须包含1—100个可解析条目；未导入')
    result = []
    for index, (data, year, unmapped) in enumerate(entries, 1):
        if year and not re.fullmatch(r'[1-9]\d{3}(?:/\d{0,2}(?:/\d{0,2})?)?', year):
            raise ValueError(f'第{index}条年份无法映射，请改为四位年份或留空：{year[:80]}')
        if '/' in year: warnings.append(f'第{index}条只保存年份，不保存月／日：{year}')
        paper = Paper(**data, year=int(year[:4]) if year else None)
        if not paper.title: warnings.append(f'第{index}条缺少题目；不补造')
        result.append({'metadata': paper.model_dump(mode='json', exclude={'id', 'created_at'}), 'unmapped': unmapped})
    return result, warnings


def existing(conn):
    return [{**db.paper_values(row), 'removed': bool(row['removed']), 'documents': []} for row in conn.execute('''
        SELECT p.* FROM papers p JOIN paper_owners o ON o.original_id=p.id WHERE o.library_id=p.id ORDER BY p.id''')]


def annotate(entries, library):
    result = []
    batch = [{**entry['metadata'], 'id': entry.get('id', f'upload-{index+1}'), 'removed': False, 'documents': []} for index, entry in enumerate(entries)]
    for index, entry in enumerate(entries):
        candidates = []
        for other in library + batch[:index] + batch[index+1:]:
            signal = signals(batch[index], other)
            if signal['candidate']:
                candidates.append({'id': other['id'], 'title': other['title'], 'removed': other['removed'], 'reasons': signal['reasons']})
        result.append({**entry, 'candidates': candidates})
    return result


def exchange(settings, content: bytes, format: str, confirmation_token: str | None = None):
    entries, warnings = parse(content, format)
    with db.transaction(settings.db_path) as conn:
        library = existing(conn)
        token = digest({'sha256': sha256(content).hexdigest(), 'format': format, 'library': library})
        if confirmation_token is not None:
            if confirmation_token != token: raise ValueError('文件、格式或文献库已变化，请重新预览；未导入')
            for entry in entries:
                paper = db.insert_paper(conn, Paper(**entry['metadata']))
                entry['id'] = str(paper.id)
        return {'confirmation_token': token, 'records': annotate(entries, library), 'warnings': warnings,
                'imported': confirmation_token is not None, 'sha256': sha256(content).hexdigest()}


def export(settings, paper_ids: list[UUID], format: str):
    with db.transaction(settings.db_path) as conn:
        rows = {row['id']: row for row in conn.execute('SELECT * FROM papers')}
        papers = []
        for key in paper_ids:
            row = rows.get(str(key))
            if row is None: raise LookupError('所选文献不存在')
            if conn.execute('SELECT library_id FROM paper_owners WHERE original_id=?', (str(key),)).fetchone()[0] != str(key):
                raise ValueError('所选条目已合并，请导出主条目或先撤销合并')
            papers.append(Paper.model_validate(db.paper_values(row)))
    warnings = list(NOTICE)
    warnings.append('仅导出已保存的元数据；未保存的编辑、标签、阅读状态、附件和笔记不在文件中。')
    if any(p.tags or p.tasks or p.methods or p.datasets or (format == 'bib' and p.keywords) for p in papers):
        warnings.append('本次存在标签／任务／方法／数据集或BibTeX关键词等未映射字段，请保留原库。')
    if format == 'bib':
        entries = []
        for paper in papers:
            fields = {key: getattr(paper, key) for key in ('title', 'abstract', 'doi') if getattr(paper, key)}
            if paper.year: fields['year'] = str(paper.year)
            if paper.authors: fields['author'] = ' and '.join('{'+name+'}' for name in paper.authors)
            for field, key in BIB_IDENTIFIERS.items():
                if key in paper.identifiers:
                    if len(paper.identifiers[key]) != 1: raise ValueError(f'{key}有多个值，当前BibTeX单字段不能无损映射；请使用RIS或单独核对')
                    fields[field] = paper.identifiers[key][0]
            entries.append(Entry('misc', 'papertrail'+paper.id.hex, [Field(k, v) for k, v in fields.items()]))
        text = bibtexparser.write_string(bibtexparser.Library(entries))
        # Refuse unbalanced braces/backslashes or syntax-bearing values that change a field.
        checked, _ = parse(text.encode('utf-8'), 'bib')
        for paper, check in zip(papers, checked, strict=True):
            if any(check['metadata'][key] != getattr(paper, key) for key in ('title', 'authors', 'abstract', 'doi', 'year', 'identifiers')):
                raise ValueError('BibTeX字符无法无损序列化，请核对括号／转义或改用RIS；未生成文件')
        warnings.append('accessionnumber／serialnumber是本应用保留登录号／未分类SN的扩展字段，其他工具可能忽略。')
    else:
        records = []
        for paper in papers:
            record = {'type_of_reference': 'GEN', 'id': 'papertrail'+paper.id.hex,
                      'title': paper.title, 'authors': paper.authors, 'abstract': paper.abstract, 'doi': paper.doi,
                      'keywords': paper.keywords, 'urls': paper.identifiers.get('url', []),
                      'issn': [v for key in ('isbn', 'issn', 'serial_number') for v in paper.identifiers.get(key, [])],
                      'accession_number': [v for key in ('eprint', 'accession_number') for v in paper.identifiers.get(key, [])]}
            if paper.year: record['year'] = str(paper.year)
            # A tagged newline can become a new RIS field/record, so don't export ambiguous text.
            if any('\n' in v or '\r' in v for value in record.values() for v in (value if isinstance(value, list) else [value])):
                raise ValueError('RIS单字段含换行，当前无法无损导出；请改用BibTeX或在元数据中明确合为单行')
            records.append(record)
        text = rispy.dumps(records, implementation=PlainRisWriter, list_tags=[*rispy.LIST_TYPE_TAGS, 'SN', 'AN'])
        checked, _ = parse(text.encode('utf-8'), 'ris')
        for paper, check in zip(papers, checked, strict=True):
            same = all(check['metadata'][key] == getattr(paper, key) for key in ('title', 'authors', 'abstract', 'doi', 'year', 'keywords'))
            before = sorted(v for values in paper.identifiers.values() for v in values)
            after = sorted(v for values in check['metadata']['identifiers'].values() for v in values)
            if not same or before != after:
                raise ValueError('RIS字符无法无损序列化，请核对首尾空格／续行或改用BibTeX；未生成文件')
        warnings.append('ISBN／ISSN以SN输出、eprint以AN输出，值保留；RIS再次导入不会推断它们的原分类。')
    return {'text': text, 'filename': f'papertrail.{format}', 'count': len(papers), 'warnings': warnings}
