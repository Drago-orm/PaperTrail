"""Literal protected spans and ID-aligned translations through the shared client."""
from dataclasses import dataclass
import hashlib
import json
import re
from uuid import UUID

from pydantic import Field, model_validator

from app.agents.client import complete
from app.config import PROJECT_ROOT
from app.schemas import EvidenceBlock, Schema

PROMPT_VERSION = 'translation-v1'
PROTECTION_VERSION = 'literal-1'
TOKEN = re.compile(r'\[\[PT_[0-9a-f]{12}_\d+\]\]')
NUMBER = r'(?<![A-Za-z0-9_.])[-+−]?(?:\d+(?:[.,]\d+)*|\.\d+)(?:[eE][-+−]?\d+)?(?:[ \t]*(?:[A-Za-zµμ°℃][A-Za-z0-9µμ°℃/·²³^-]{0,20}|[%％‰]))?'
IDENTIFIER = r'(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_.+-]*\d[A-Za-z0-9_.+-]*(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_]*_[A-Za-z0-9_]+(?![A-Za-z0-9_])'
LITERAL = r'```[\s\S]*?```|~~~[\s\S]*?~~~|\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$[^$\n]+\$|`[^`\n]+`|https?://[^\s]+|\[[^\[\]\n]{1,300}\]|【[^【】\n]{1,300}】|[（(][^()（）\n]{0,120}(?:19|20)\d{2}[a-z]?[^()（）\n]{0,40}[)）]'
CODE = re.compile(r'(?m)^\s*(?:```|~~~|def\s|class\s|import\s|from\s.+\simport\s|return\b|for\s.+:|if\s.+:|(?:const|let|var|function)\s|#include|>>>|print\s*\()|[=<>≤≥^±∑∫√∂∇∏∈∉∀∃≈≠∞]|\\(?:frac|sum|int|begin)\b')


class Term(Schema):
    source: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=80)

    @model_validator(mode='after')
    def literal_values(self):
        self.source, self.target = self.source.strip(), self.target.strip()
        if not self.source or not self.target or any(char in self.source+self.target for char in ('\n', '\r')) or '[[PT_' in self.source+self.target:
            raise ValueError('术语须为非空单行，不能包含保护标记')
        critical = re.compile(LITERAL+'|'+IDENTIFIER+'|'+NUMBER)
        if critical.findall(self.source) != critical.findall(self.target):
            raise ValueError('术语映射不得改写数值、单位、代码／公式或带数字标识符')
        if (CODE.search(self.source) or CODE.search(self.target)) and self.source != self.target:
            raise ValueError('术语映射不得改写或新增公式／代码表达式')
        return self


class Translated(Schema):
    evidence_id: UUID
    text: str = Field(min_length=1, max_length=12000)


class Output(Schema):
    translations: list[Translated] = Field(min_length=1, max_length=6)


@dataclass
class Protected:
    block: EvidenceBlock
    masked: str
    tokens: list[tuple[str, str]]
    terms: list[str]
    local_status: str | None

    def restore(self, text: str) -> str:
        if not text.strip() or TOKEN.findall(text) != [token for token, _ in self.tokens]:
            raise ValueError('模型遗漏、改写或重排保护片段，未保存译文')
        plain = TOKEN.sub('', text)
        if re.search(LITERAL+'|'+IDENTIFIER+'|'+NUMBER, plain) or CODE.search(plain):
            raise ValueError('模型新增了未保护的数值、代码或公式标记，未保存译文')
        for token, original in self.tokens:
            text = text.replace(token, original)
        return text


def protect(block: EvidenceBlock, glossary: list[Term]) -> Protected:
    if '[[PT_' in block.text:
        raise ValueError('原文含冲突保护标记，无法安全翻译')
    nonce = hashlib.sha256((str(block.id)+block.text).encode()).hexdigest()[:12]
    tokens, used = [], []
    terms = {term.source: term.target for term in glossary}
    patterns = []
    for term in sorted(terms, key=len, reverse=True):
        start = r'(?<![A-Za-z0-9_])' if term[0].isascii() and term[0].isalnum() else ''
        end = r'(?![A-Za-z0-9_])' if term[-1].isascii() and term[-1].isalnum() else ''
        patterns.append(start+re.escape(term)+end)
    pattern = LITERAL+'|'+('|'.join(patterns)+'|' if patterns else '')+IDENTIFIER+'|'+NUMBER
    def substitute(match):
        value = match.group()
        token = f'[[PT_{nonce}_{len(tokens)}]]'
        if value in terms:
            used.append(value)
        tokens.append((token, terms.get(value, value)))
        return token
    masked = re.sub(pattern, substitute, block.text)
    plain = TOKEN.sub('', masked)
    if CODE.search(plain):
        return Protected(block, block.text, [], [], 'preserved')
    local = None if any(char.isalpha() for char in plain) else 'glossary' if used else 'preserved'
    return Protected(block, masked, tokens, list(dict.fromkeys(used)), local)


def translate(settings, blocks: list[Protected], target: str, glossary: list[Term]):
    prompt = (PROJECT_ROOT/f'prompts/{PROMPT_VERSION}.md').read_text(encoding='utf-8')
    prompt += '\nJSON Schema:\n'+json.dumps(Output.model_json_schema(), ensure_ascii=False)
    return complete(settings, prompt, json.dumps({'target_language': target,
        'glossary': [term.model_dump() for term in glossary],
        'blocks': [{'evidence_id': str(value.block.id), 'text': value.masked} for value in blocks]}, ensure_ascii=False))


def validate(content: str, blocks: list[Protected]) -> dict[UUID, str]:
    output = Output.model_validate_json(content)
    expected = {value.block.id: value for value in blocks}
    keys = [value.evidence_id for value in output.translations]
    if len(keys) != len(set(keys)) or set(keys) != set(expected):
        raise ValueError('译文证据ID重复、遗漏或越出本次范围，未保存')
    return {item.evidence_id: expected[item.evidence_id].restore(item.text) for item in output.translations}
