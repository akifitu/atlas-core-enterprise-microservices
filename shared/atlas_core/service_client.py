import json
from typing import Any, Dict, Optional, Tuple
from urllib import error, request


def request_json(
    method: str,
    base_url: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 5,
) -> Tuple[int, Any]:
    normalized_base = base_url.rstrip("/")
    data = None
    final_headers = {
        "Accept": "application/json",
    }
    if headers:
        final_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        final_headers["Content-Type"] = "application/json"

    http_request = request.Request(
        normalized_base + path,
        data=data,
        method=method.upper(),
        headers=final_headers,
    )

    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            raw_body = response.read()
            body = json.loads(raw_body.decode("utf-8")) if raw_body else None
            return response.status, body
    except error.HTTPError as exc:
        raw_body = exc.read()
        body = json.loads(raw_body.decode("utf-8")) if raw_body else None
        return exc.code, body
