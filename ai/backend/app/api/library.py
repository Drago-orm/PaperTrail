import json
from contextlib import closing
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import JsonValue, ValidationError

from app import db
from app.config import Settings
from app.schemas import Document, Paper
from app.services.storage import document_path

ARRAYS = ('authors', 'keywords', 'tags', 'tasks', 'methods', 'datasets')
EDITABLE = ('title', 'abstract', *ARRAYS, 'reading_status')


class LibraryPaper(Paper):
    removed: bool
    documents: list[Document]


def paper_values(row):
    values = dict(row)
    values.pop('removed')
    for name in ARRAYS:
        values[name] = json.loads(values[name])
    return values


def library_router(settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get('/api/papers', response_model=list[LibraryPaper])
    def list_papers(q: str = '', title: str = '', author: str = '', abstract: str = '',
                    keyword: str = '', tag: str = '', reading_status: str = '', removed: bool = False):
        clauses = ['removed=?']
        parameters: list[str | int] = [int(removed)]
        for column, value in [('title', title), ('authors', author), ('abstract', abstract),
                              ('keywords', keyword), ('tags', tag), ('reading_status', reading_status)]:
            if value:
                clauses.append(f'{column}=?' if column == 'reading_status' else f'instr(lower({column}),lower(?))>0')
                parameters.append(value)
        if q:
            clauses.append("instr(lower(title||authors||abstract||keywords||tags),lower(?))>0")
            parameters.append(q)
        with closing(db.connect(settings.db_path)) as conn:
            rows = conn.execute('SELECT * FROM papers WHERE '+' AND '.join(clauses)+' ORDER BY created_at DESC,id', parameters).fetchall()
            return [LibraryPaper(**paper_values(row), removed=bool(row['removed']), documents=[
                Document.model_validate(dict(doc)) for doc in conn.execute(
                    'SELECT * FROM documents WHERE paper_id=? ORDER BY version DESC', (row['id'],))]) for row in rows]

    @router.patch('/api/papers/{paper_id}', response_model=Paper)
    def edit_paper(paper_id: UUID, metadata: dict[str, JsonValue]):
        if set(metadata) - set(EDITABLE):
            raise HTTPException(422, '包含不可编辑字段')
        with db.transaction(settings.db_path) as conn:
            row = conn.execute('SELECT * FROM papers WHERE id=?', (str(paper_id),)).fetchone()
            if row is None:
                raise HTTPException(404, '文献不存在')
            try:
                paper = Paper.model_validate({**paper_values(row), **metadata})
            except ValidationError as exc:
                raise HTTPException(422, '元数据类型或阅读状态无效') from exc
            conn.execute('UPDATE papers SET '+','.join(f'{field}=?' for field in EDITABLE)+' WHERE id=?',
                         [json.dumps(getattr(paper, field), ensure_ascii=False) if field in ARRAYS else getattr(paper, field)
                          for field in EDITABLE]+[str(paper_id)])
        return paper

    @router.put('/api/papers/{paper_id}/visibility')
    def visibility(paper_id: UUID, removed: bool):
        with db.transaction(settings.db_path) as conn:
            if conn.execute('UPDATE papers SET removed=? WHERE id=?', (int(removed), str(paper_id))).rowcount != 1:
                raise HTTPException(404, '文献不存在')
        return {'removed': removed}

    @router.get('/api/documents/{document_id}/file')
    def file(document_id: UUID):
        try:
            path = document_path(settings.db_path, settings.data_dir, document_id)
        except (LookupError, FileNotFoundError) as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(path, media_type='application/pdf', filename=f'{document_id}.pdf', content_disposition_type='inline')

    return router
