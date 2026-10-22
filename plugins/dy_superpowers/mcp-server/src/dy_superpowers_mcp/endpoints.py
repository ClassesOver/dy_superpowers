"""出站目标只允许受信任配置显式列出的精确 origin。"""

import ipaddress
import os
import re
from urllib.parse import urlsplit


def _origin(url: str) -> str:
    try:
        if not isinstance(url, str) or any(char.isspace() or ord(char) < 32 for char in url) or '\\' in url:
            raise ValueError
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or '?' in url or '#' in url):
            raise ValueError
        host = parsed.hostname.encode('idna').decode('ascii').lower()
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80)
        if not 1 <= port <= 65535:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if (len(host) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                                      for part in host.split('.'))):
                raise ValueError
        else:
            # 元数据、未指定、组播目标即使误加到允许列表也拒绝。
            if address.is_link_local or address.is_unspecified or address.is_multicast:
                raise ValueError
            host = f'[{host}]' if address.version == 6 else host
        return f'{parsed.scheme}://{host}:{port}'
    except (ValueError, UnicodeError) as exc:
        raise ValueError('端点 URL 不合法：仅允许带合法主机和端口的 HTTP(S)，禁止凭据、查询及片段') from exc


def validate_endpoint(url: str, allowed_variable: str, *, local_default: str | None = None) -> str:
    origin = _origin(url)
    if local_default is not None and url == local_default:
        return url
    allowed = set()
    for item in os.environ.get(allowed_variable, '').split(','):
        if not item.strip():
            continue
        candidate = item.strip()
        parsed = urlsplit(candidate)
        if parsed.path not in ('', '/'):
            raise ValueError(f'{allowed_variable} 只接受精确 origin，不能包含路径')
        allowed.add(_origin(candidate))
    if origin not in allowed:
        raise ValueError(f'端点未授权；请在受信任的 {allowed_variable} 中显式设置精确 scheme://host:port')
    return url
