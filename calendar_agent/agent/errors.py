"""User-facing diagnostics must not echo rejected model payloads."""
from pydantic import ValidationError
import httpx


def public_error(error):
    if isinstance(error, ValidationError):
        fields = ', '.join('.'.join(str(p) for p in item['loc']) or 'JSON' for item in error.errors(include_input=False))
        return '模型协议校验失败：'+fields
    if isinstance(error, httpx.HTTPError):
        return '外部服务请求失败：'+type(error).__name__
    return str(getattr(error, 'detail', error))[:1000]
