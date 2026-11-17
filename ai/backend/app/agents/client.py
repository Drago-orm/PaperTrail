"""One bounded OpenAI-compatible chat client; secrets never enter result records."""
from dataclasses import dataclass
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.config import Settings

TIMEOUT_SECONDS = 60
MAX_CALLS = 2
MAX_RESPONSE_BYTES = 256 * 1024


class ModelError(RuntimeError):
    def __init__(self, message: str, calls: int = 0):
        super().__init__(message)
        self.calls = calls


@dataclass
class Completion:
    content: str
    usage: dict[str, int] | None
    calls: int


def complete(settings: Settings, system: str, user: str) -> Completion:
    missing, errors = settings.model_status()
    if missing or errors:
        raise ModelError('模型配置缺失或无效：' + '、'.join(missing + errors))
    parsed = urlsplit(settings.model_base_url)
    if parsed.query or parsed.fragment:
        raise ModelError('模型基址不能包含查询参数或片段')
    body = json.dumps({
        'model': settings.model_name,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'response_format': {'type': 'json_object'}, 'max_tokens': 4096,
    }).encode()
    request = Request(settings.model_base_url.rstrip('/') + '/chat/completions', data=body,
                      headers={'Authorization': 'Bearer ' + settings.model_api_key, 'Content-Type': 'application/json'})
    for attempt in range(1, MAX_CALLS + 1):
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ModelError('模型响应超过256 KiB上限', attempt)
            payload = json.loads(raw)
            choice = payload['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise ModelError('模型输出未正常完成（可能截断/拒绝）', attempt)
            content = choice['message']['content']
            if not isinstance(content, str):
                raise ValueError('content')
            if settings.model_api_key in content:
                raise ModelError('模型响应包含敏感配置，未保存', attempt)
            usage = payload.get('usage')
            usage = {key: value for key, value in usage.items()
                     if key in {'prompt_tokens', 'completion_tokens', 'total_tokens'} and type(value) is int and value >= 0} if isinstance(usage, dict) else {}
            return Completion(content, usage or None, attempt)
        except HTTPError as exc:
            retry = exc.code in {429, 500, 502, 503, 504}
            message = f'模型服务HTTP {exc.code}'
            exc.close()
            if not retry or attempt == MAX_CALLS:
                raise ModelError(message, attempt) from None
        except (TimeoutError, URLError):
            if attempt == MAX_CALLS:
                raise ModelError('模型连接失败或超时（每次60秒，最多2次）', attempt) from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError('模型服务响应格式无效', attempt) from None
        time.sleep(.2)
    raise ModelError('模型调用失败', MAX_CALLS)
