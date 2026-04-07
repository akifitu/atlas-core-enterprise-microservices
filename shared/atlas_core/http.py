import json
import re
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse


class AppError(Exception):
    def __init__(self, status_code: int, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.details = details or {}


@dataclass
class Request:
    method: str
    path: str
    query: Dict[str, List[str]]
    headers: Dict[str, str]
    body: Any
    path_params: Dict[str, str]
    request_id: str

    def query_value(self, name: str, default: Optional[str] = None) -> Optional[str]:
        values = self.query.get(name)
        if not values:
            return default
        return values[0]

    def header(self, name: str, default: Optional[str] = None) -> Optional[str]:
        return self.headers.get(name.lower(), default)


@dataclass
class HttpResponse:
    body: bytes
    content_type: str
    headers: Optional[Dict[str, str]] = None


Handler = Callable[[Request], Tuple[int, Any]]


class Route:
    def __init__(self, method: str, pattern: str, handler: Handler) -> None:
        self.method = method.upper()
        self.pattern = pattern
        self.handler = handler
        regex_pattern = re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", r"(?P<\1>[^/]+)", pattern)
        self.regex = re.compile("^" + regex_pattern.rstrip("/") + "/?$")

    def match(self, method: str, path: str) -> Optional[Dict[str, str]]:
        if method.upper() != self.method:
            return None
        match = self.regex.match(path)
        if not match:
            return None
        return match.groupdict()


class ServiceApp:
    def __init__(self, service_name: str) -> None:
        self.service_name = service_name
        self.routes: List[Route] = []

    def route(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        def decorator(func: Handler) -> Handler:
            self.routes.append(Route(method, pattern, func))
            return func

        return decorator

    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        request_id = handler.headers.get("X-Request-ID") or str(uuid.uuid4())
        try:
            status_code, payload = self._dispatch(handler, request_id)
        except AppError as exc:
            status_code = exc.status_code
            payload = {
                "error": exc.message,
                "details": exc.details,
                "service": self.service_name,
                "request_id": request_id,
            }
        except Exception as exc:  # pragma: no cover - defensive boundary
            status_code = 500
            payload = {
                "error": "internal_server_error",
                "details": {"exception": str(exc)},
                "service": self.service_name,
                "request_id": request_id,
            }

        extra_headers: Dict[str, str] = {}
        if isinstance(payload, HttpResponse):
            body = payload.body
            content_type = payload.content_type
            extra_headers = payload.headers or {}
        else:
            body = json.dumps(payload).encode("utf-8")
            content_type = "application/json"
        handler.send_response(status_code)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("X-Service-Name", self.service_name)
        handler.send_header("X-Request-ID", request_id)
        for key, value in extra_headers.items():
            handler.send_header(key, value)
        handler.end_headers()
        handler.wfile.write(body)

    def _dispatch(self, handler: BaseHTTPRequestHandler, request_id: str) -> Tuple[int, Any]:
        parsed_url = urlparse(handler.path)
        path = parsed_url.path.rstrip("/") or "/"

        selected_route = None
        path_params: Dict[str, str] = {}
        for route in self.routes:
            match = route.match(handler.command, path)
            if match is not None:
                selected_route = route
                path_params = match
                break

        if selected_route is None:
            raise AppError(404, "route_not_found", {"path": path, "method": handler.command})

        content_length = int(handler.headers.get("Content-Length", "0") or "0")
        raw_body = handler.rfile.read(content_length) if content_length > 0 else b""
        payload = None
        if raw_body:
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise AppError(400, "invalid_json", {"reason": str(exc)}) from exc

        request = Request(
            method=handler.command,
            path=path,
            query=parse_qs(parsed_url.query),
            headers={key.lower(): value for key, value in handler.headers.items()},
            body=payload,
            path_params=path_params,
            request_id=request_id,
        )
        return selected_route.handler(request)


def build_handler(app: ServiceApp) -> type:
    class AtlasHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            app.handle(self)

        def do_POST(self) -> None:  # noqa: N802
            app.handle(self)

        def do_PATCH(self) -> None:  # noqa: N802
            app.handle(self)

        def do_PUT(self) -> None:  # noqa: N802
            app.handle(self)

        def log_message(self, fmt: str, *args: Any) -> None:
            return None

    return AtlasHandler


def run_service(app: ServiceApp, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), build_handler(app))
    print("{0} listening on http://{1}:{2}".format(app.service_name, host, port))
    server.serve_forever()
