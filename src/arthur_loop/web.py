from __future__ import annotations

import json
import secrets
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from arthur_loop.config import load_config
from arthur_loop.queue_ledger import QueueJob, QueueLedger, isoformat, read_jsonl
from arthur_loop.status import clear_session, collect_status, status_to_dict


DEFAULT_PORT = 7433

# static files we are willing to serve, nothing else
STATIC_FILES = {
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}

# artifact viewer may only open loop text files
READABLE_SUFFIXES = {".md", ".jsonl", ".txt", ".log", ".json"}


def _camel_key(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _camelize(value: Any) -> Any:
    """Recursively rewrite dict keys snake_case -> camelCase for the JS frontend."""

    if isinstance(value, dict):
        return {_camel_key(k): _camelize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


class WebApp:
    """Shared state for the request handler: instance root, config, session token."""

    def __init__(self, root: Path):
        self.root = root
        self.config = load_config(root)
        self.token = secrets.token_urlsafe(24)

    # ------------------------------------------------------------------ reads

    def status_payload(self) -> dict[str, Any]:
        snapshot = collect_status(
            self.root,
            reserve_percent=float(self.config["reserve_policy"]["minimum_reserve_percent"]),
            quota_enabled=bool(self.config["components"]["resource_governor"]),
        )
        # status_to_dict is the shared, parity-locked serializer (snake_case);
        # the browser gets a camelCase copy so the frontend has one convention.
        payload = _camelize(status_to_dict(snapshot))
        payload["server"] = {
            "root": str(self.root),
            "instance": self.root.name or "arthur-loop",
            "advisor": self.config["advisor"]["adapter"],
            "executor": self.config["executor"]["adapter"],
        }
        return payload

    def events_payload(self, limit: int) -> list[dict[str, Any]]:
        events = read_jsonl(self.root / "queue/events.jsonl")
        return list(reversed(events[-limit:]))

    def artifacts_payload(self, project_id: str) -> list[dict[str, Any]]:
        index = self.root / "projects" / project_id / "artifacts" / "chatgpt" / "index.jsonl"
        latest: dict[str, dict[str, Any]] = {}
        for record in read_jsonl(index):
            latest[record.get("artifact_id", "")] = record
        return sorted(latest.values(), key=lambda r: r.get("created_at", ""), reverse=True)

    def read_instance_file(self, rel_path: str) -> str:
        """Resolve a repo-relative text file, refusing anything outside the root."""

        candidate = (self.root / rel_path).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError:
            raise PermissionError("path escapes the instance root")
        if candidate.suffix.lower() not in READABLE_SUFFIXES:
            raise PermissionError("only loop text files can be viewed")
        if not candidate.is_file():
            raise FileNotFoundError(rel_path)
        return candidate.read_text(encoding="utf-8")

    # ---------------------------------------------------------------- actions

    def answer_decision(self, title: str, answer: str) -> dict[str, Any]:
        """Mark one open human decision answered, recording the answer inline."""

        title = title.strip()
        answer = answer.strip()
        if not title or not answer:
            raise ValueError("both a decision title and an answer are required")

        path = self.root / "human-decisions/open.md"
        if not path.exists():
            raise ValueError("human-decisions/open.md does not exist")

        lines = path.read_text(encoding="utf-8").splitlines()
        in_section = False
        answered = False
        out: list[str] = []
        for line in lines:
            if line.startswith("## "):
                in_section = line[3:].strip() == title
            if in_section and not answered and line.strip().lower().startswith("status:") and "open" in line.lower():
                out.append("Status: `ANSWERED`")
                out.append("")
                out.append(f"**Answer** ({isoformat()}): {answer}")
                answered = True
                continue
            out.append(line)

        if not answered:
            raise ValueError(f"no open decision titled {title!r}")

        path.write_text("\n".join(out) + "\n", encoding="utf-8")
        QueueLedger(self.root).append_event(
            "__decision__", "decision_answered", {"title": title}
        )
        return {"title": title, "status": "ANSWERED"}

    def recover_job(self, job_id: str, requeue: bool) -> dict[str, Any]:
        ledger = QueueLedger(self.root)
        job = ledger.transition(job_id, "needs_recovery", error="parked from the web console")
        if requeue:
            job = ledger.transition(job_id, "queued")
        return job.to_record()

    def create_job(self, body: dict[str, Any]) -> dict[str, Any]:
        required = ["job_id", "project_id", "target_chat_title", "target_chat_url"]
        missing = [key for key in required if not str(body.get(key, "")).strip()]
        if missing:
            raise ValueError(f"missing fields: {', '.join(missing)}")

        ledger = QueueLedger(self.root)
        job_id = str(body["job_id"]).strip()
        if job_id in ledger.latest_jobs():
            raise ValueError(f"queue job {job_id} already exists")
        job = QueueJob(
            job_id=job_id,
            project_id=str(body["project_id"]).strip(),
            target_chat_title=str(body["target_chat_title"]).strip(),
            target_chat_url=str(body["target_chat_url"]).strip(),
            prompt_path=str(body.get("prompt_path") or "").strip() or None,
            expected_marker=str(body.get("expected_marker") or "").strip() or None,
        )
        ledger.record_job(job)
        return job.to_record()

    def clear_session_action(self, session_id: str) -> dict[str, Any]:
        cleared = clear_session(self.root, session_id)
        return {"session_id": session_id, "cleared": cleared}


class Handler(BaseHTTPRequestHandler):
    server_version = "ArthurLoopWeb"
    app: WebApp  # assigned by serve()

    # silence per-request stderr logging; the console prints its own line
    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    # -------------------------------------------------------------- plumbing

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost", "[::1]")

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, content_type: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            raise ValueError("request body required")
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    # ---------------------------------------------------------------- routes

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if not self._host_allowed():
            return self._fail(HTTPStatus.FORBIDDEN, "arthur web only answers localhost")

        url = urlparse(self.path)
        query = parse_qs(url.query)

        try:
            if url.path == "/":
                html = _read_ui("index.html").replace("__ARTHUR_TOKEN__", self.app.token)
                return self._send_text(html, "text/html; charset=utf-8")
            if url.path.startswith("/assets/"):
                name = url.path.removeprefix("/assets/")
                if name not in STATIC_FILES:
                    return self._fail(HTTPStatus.NOT_FOUND, "unknown asset")
                return self._send_text(_read_ui(name), STATIC_FILES[name])
            if url.path == "/api/status":
                return self._send_json(self.app.status_payload())
            if url.path == "/api/events":
                limit = min(int(query.get("n", ["120"])[0]), 500)
                return self._send_json(self.app.events_payload(limit))
            if url.path == "/api/artifacts":
                project = query.get("project", [""])[0]
                if not project:
                    return self._fail(HTTPStatus.BAD_REQUEST, "project query param required")
                return self._send_json(self.app.artifacts_payload(project))
            if url.path == "/api/file":
                rel = query.get("path", [""])[0]
                try:
                    return self._send_text(self.app.read_instance_file(rel), "text/plain; charset=utf-8")
                except PermissionError as exc:
                    return self._fail(HTTPStatus.BAD_REQUEST, str(exc))
                except FileNotFoundError:
                    return self._fail(HTTPStatus.NOT_FOUND, "no such file")
            return self._fail(HTTPStatus.NOT_FOUND, "unknown route")
        except Exception as exc:  # pragma: no cover - last-resort guard
            return self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        if not self._host_allowed():
            return self._fail(HTTPStatus.FORBIDDEN, "arthur web only answers localhost")
        if self.headers.get("X-Arthur-Token") != self.app.token:
            return self._fail(HTTPStatus.FORBIDDEN, "missing or wrong session token")

        try:
            body = self._read_body()
            if self.path == "/api/actions/answer-decision":
                return self._send_json(
                    self.app.answer_decision(str(body.get("title", "")), str(body.get("answer", "")))
                )
            if self.path == "/api/actions/recover-job":
                return self._send_json(
                    self.app.recover_job(str(body.get("job_id", "")), bool(body.get("requeue")))
                )
            if self.path == "/api/actions/create-job":
                return self._send_json(self.app.create_job(body))
            if self.path == "/api/actions/clear-session":
                return self._send_json(self.app.clear_session_action(str(body.get("session_id", ""))))
            return self._fail(HTTPStatus.NOT_FOUND, "unknown action")
        except (ValueError, KeyError) as exc:
            return self._fail(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # pragma: no cover - last-resort guard
            return self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))


def _read_ui(name: str) -> str:
    return (resources.files("arthur_loop") / "webui" / name).read_text(encoding="utf-8")


def make_server(root: Path, port: int = 0) -> ThreadingHTTPServer:
    """Build (but do not run) the web console server. port=0 picks a free port."""

    app = WebApp(root)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.arthur_app = app  # type: ignore[attr-defined]
    return server


def cmd_web(args: Any) -> int:
    root = Path(args.root).resolve()
    try:
        server = make_server(root, port=args.port)
    except OSError as exc:
        print(f"error: could not bind 127.0.0.1:{args.port} ({exc})", file=sys.stderr)
        return 2

    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"arthur web console: {url}")
    print(f"instance: {root}")
    print("read-only for agents; humans get four actions. Ctrl+C stops it.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nweb console stopped")
    finally:
        server.server_close()
    return 0


def add_web_parser(subparsers: Any) -> None:
    web = subparsers.add_parser("web", help="Serve the local web console for this instance")
    web.add_argument("--root", default=".", help="Arthur Loop instance root")
    web.add_argument("--port", type=int, default=DEFAULT_PORT)
    web.add_argument("--no-open", action="store_true", help="Do not open the browser")
    web.set_defaults(func=cmd_web)
