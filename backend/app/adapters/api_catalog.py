"""Model discovery cache isolated by endpoint, policy and credential version."""
import hashlib
import json
import threading
from datetime import datetime, timezone

from backend.app.adapters.api_transport import APIError, request
from backend.app.storage.api_connections import read_key

_cache = {}
_lock = threading.Lock()


def cache_key(settings, cid, connection):
    fingerprint = json.dumps([str(settings.database_path.resolve()), cid, connection['base_url'],
                              connection['protocol'], connection.get('key_ref'), connection.get('allow_private'),
                              connection.get('enabled')])
    return hashlib.sha256(fingerprint.encode()).hexdigest()


def catalog(settings, cid, connection, *, refresh=False):
    version = cache_key(settings, cid, connection)
    with _lock:
        previous = _cache.get(version)
    if not refresh:
        return dict(previous) if previous else dict(connection_id=cid, status='unsupported' if connection is None else 'error',
                                                    models=[], fetched_at=None, error='Catalog has not been fetched')
    try:
        key = read_key(settings, connection)
        models, seen, cursors = [], set(), set()
        path = '/models'
        total = 0
        for _ in range(50):
            with request(connection, key, 'GET', path) as response:
                raw = response.read(4_000_001)
                total += len(raw)
                if total > 4_000_000:
                    raise APIError('response_limit')
                if key and key.encode() in raw:
                    raise APIError('protocol')
                data = json.loads(raw)
                if key and key in json.dumps(data, ensure_ascii=False):
                    raise APIError('protocol')
            if not isinstance(data.get('data'), list):
                raise APIError('protocol')
            for item in data['data']:
                if not isinstance(item, dict) or not isinstance(item.get('id'), str):
                    raise APIError('protocol')
                mid = item['id']
                if not mid.strip() or len(mid) > 200 or any(ord(c) < 32 for c in mid):
                    raise APIError('protocol')
                if mid in seen:
                    continue
                seen.add(mid)
                vendor = item.get('owned_by')
                label = item.get('display_name') or mid
                models.append(dict(id=mid, label=label if isinstance(label, str) else mid,
                                   vendor=vendor if isinstance(vendor, str) else None))
            if not data.get('has_more'):
                break
            cursor = data.get('last_id')
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise APIError('protocol')
            cursors.add(cursor)
            from urllib.parse import urlencode
            path = '/models?' + urlencode({'after_id': cursor})
        else:
            raise APIError('response_limit')
        result = dict(connection_id=cid, status='ready', models=models,
                      fetched_at=datetime.now(timezone.utc).isoformat(), error=None)
    except (APIError, ValueError, TypeError, AttributeError) as exc:
        result = dict(previous) if previous else dict(connection_id=cid, models=[], fetched_at=None)
        result.update(status='stale' if result['fetched_at'] else 'error',
                      error=str(exc) if isinstance(exc, APIError) else 'API request failed: protocol')
    with _lock:
        if len(_cache) >= 256 and version not in _cache:
            _cache.pop(next(iter(_cache)))
        _cache[version] = result
    return dict(result)
