from __future__ import annotations

import json
import secrets
import sys
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from arthur_loop.browser_lock import is_fresh, lock_path, read_lock
from arthur_loop.config import load_config
from arthur_loop.queue_ledger import (
    TERMINAL_STATUSES,
    QueueJob,
    QueueLedger,
    isoformat,
    read_jsonl,
)
from arthur_loop.status import clear_session, collect_status, status_to_dict
from arthur_loop.tick import OPEN_STATUS_RE, SECTION_RE


DEFAULT_PORT = 7433

# static files we are willing to serve, nothing else
STATIC_FILES = {
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}

# artifact viewer may only open loop text files
READABLE_SUFFIXES = {".md", ".jsonl", ".txt", ".log", ".json"}

# viewer cap — loop logs grow without bound, don't slurp them whole
MAX_VIEW_BYTES = 2_000_000


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
        # ThreadingHTTPServer: writes are read-modify-write, serialize them
        self._write_lock = threading.Lock()

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
        bodies = self._decision_bodies()
        for decision in payload["decisions"]:
            decision["body"] = bodies.get(decision["title"], "")[:600]
        payload["quarantine"] = self.quarantine_payload(
            [project["projectId"] for project in payload["projects"]]
        )
        payload["server"] = {
            "root": str(self.root),
            "instance": self.root.name or "arthur-loop",
            "advisor": self.config["advisor"]["adapter"],
            "executor": self.config["executor"]["adapter"],
        }
        return payload

    def _decision_bodies(self) -> dict[str, str]:
        """Map open-decision titles to their question text (status line stripped)."""

        path = self.root / "human-decisions/open.md"
        if not path.exists():
            return {}
        bodies: dict[str, str] = {}
        title: str | None = None
        buf: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                if title is not None:
                    bodies[title] = "\n".join(buf).strip()
                title = line[3:].strip()
                buf = []
            elif title is not None and not line.strip().lower().startswith("status:"):
                buf.append(line)
        if title is not None:
            bodies[title] = "\n".join(buf).strip()
        return bodies

    def quarantine_payload(self, project_ids: list[str]) -> list[dict[str, Any]]:
        """Artifacts whose control block failed validation, across all projects."""

        quarantined: list[dict[str, Any]] = []
        for project_id in project_ids:
            index = self.root / "projects" / project_id / "artifacts" / "chatgpt" / "index.jsonl"
            latest: dict[str, dict[str, Any]] = {}
            for record in read_jsonl(index):
                latest[record.get("artifact_id", "")] = record
            for record in latest.values():
                if record.get("control_block_valid") is False:
                    quarantined.append(
                        {
                            "projectId": project_id,
                            "kind": record.get("kind", "artifact"),
                            "path": record.get("path", ""),
                            "createdAt": record.get("created_at", ""),
                            "reasons": record.get("control_block_reasons") or "",
                        }
                    )
        quarantined.sort(key=lambda record: record["createdAt"], reverse=True)
        return quarantined[:12]

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
        size = candidate.stat().st_size
        if size <= MAX_VIEW_BYTES:
            return candidate.read_text(encoding="utf-8")
        with candidate.open("rb") as fh:
            fh.seek(size - MAX_VIEW_BYTES)
            tail = fh.read().decode("utf-8", errors="replace")
        return f"… (file is {size:,} bytes; showing the last {MAX_VIEW_BYTES:,})\n{tail}"

    # ---------------------------------------------------------------- actions

    @staticmethod
    def _defuse_answer(answer: str) -> str:
        """Backslash-escape lines that could forge sections or status flips in open.md.

        Answers are often drafted by an agent or pasted from an artifact, so a
        multiline answer containing "## Title" / "Status: OPEN" would otherwise
        parse as a brand-new open decision on the next tick.
        """

        out = []
        for line in answer.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.lower().startswith("status:"):
                indent = line[: len(line) - len(stripped)]
                line = f"{indent}\\{stripped}"
            out.append(line)
        return "\n".join(out)

    def answer_decision(self, title: str, answer: str) -> dict[str, Any]:
        """Mark one open human decision answered, recording the answer inline."""

        title = title.strip()
        answer = self._defuse_answer(answer.strip())
        if not title or not answer:
            raise ValueError("both a decision title and an answer are required")

        path = self.root / "human-decisions/open.md"
        if not path.exists():
            raise ValueError("human-decisions/open.md does not exist")

        with self._write_lock:
            lines = path.read_text(encoding="utf-8").splitlines()
            in_section = False
            answered = False
            out: list[str] = []
            for line in lines:
                section = SECTION_RE.match(line)
                if section:
                    in_section = section.group(1) == title
                # same regex the tick classifier uses, so the console can never
                # disagree with the loop about which decisions are open
                if in_section and not answered and OPEN_STATUS_RE.match(line):
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
        with self._write_lock:
            current = ledger.latest_jobs().get(job_id)
            if current is None:
                raise ValueError(f"unknown queue job: {job_id}")
            if current.status in TERMINAL_STATUSES:
                raise ValueError(f"{job_id} is {current.status}; finished jobs are not recoverable")
            # keep the diagnostic around instead of clobbering it with our note
            note = "parked from the web console"
            if current.last_error:
                note = f"{current.last_error} — {note}"
            job = ledger.transition(job_id, "needs_recovery", error=note)
            if requeue:
                job = ledger.transition(job_id, "queued")
        return job.to_record()

    def create_job(self, body: dict[str, Any]) -> dict[str, Any]:
        required = ["job_id", "project_id", "target_chat_title", "target_chat_url"]
        missing = [
            key for key in required
            if not isinstance(body.get(key), str) or not body[key].strip()
        ]
        if missing:
            raise ValueError(f"missing fields: {', '.join(missing)}")

        ledger = QueueLedger(self.root)
        job = QueueJob(
            job_id=body["job_id"].strip(),
            project_id=body["project_id"].strip(),
            target_chat_title=body["target_chat_title"].strip(),
            target_chat_url=body["target_chat_url"].strip(),
            prompt_path=str(body.get("prompt_path") or "").strip() or None,
            expected_marker=str(body.get("expected_marker") or "").strip() or None,
        )
        with self._write_lock:
            if job.job_id in ledger.latest_jobs():
                raise ValueError(f"queue job {job.job_id} already exists")
            ledger.record_job(job)
        return job.to_record()

    def clear_session_action(self, session_id: str) -> dict[str, Any]:
        cleared = clear_session(self.root, session_id)
        return {"session_id": session_id, "cleared": cleared}

    def break_lock(self) -> dict[str, Any]:
        """Remove a STALE browser lock. A fresh lock means the holder is active: refuse."""

        with self._write_lock:
            lock = read_lock(self.root)
            if lock is None:
                return {"broken": False, "reason": "no lock held"}
            if is_fresh(lock):
                raise ValueError(f"lock is fresh; {lock.holder} is still active. Refusing to break it.")
            lock_path(self.root).unlink(missing_ok=True)
        QueueLedger(self.root).append_event(
            "__browser_lock__", "lock_broken", {"holder": lock.holder, "via": "web-console"}
        )
        return {"broken": True, "holder": lock.holder}


class Handler(BaseHTTPRequestHandler):
    server_version = "ArthurLoopWeb"
    app: WebApp  # assigned by serve()

    # silence per-request stderr logging; the console prints its own line
    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    # -------------------------------------------------------------- plumbing

    def _host_allowed(self) -> bool:
        raw = (self.headers.get("Host") or "").strip().lower()
        if raw.startswith("["):  # bracketed IPv6, [::1]:7433
            host = raw.split("]", 1)[0] + "]"
        else:
            host = raw.split(":", 1)[0]
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
                try:
                    requested = int(query.get("n", ["120"])[0])
                except ValueError:
                    return self._fail(HTTPStatus.BAD_REQUEST, "n must be an integer")
                return self._send_json(self.app.events_payload(max(1, min(requested, 500))))
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
        except Exception:  # pragma: no cover - last-resort guard
            traceback.print_exc()
            return self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error — see the arthur web terminal")

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        if not self._host_allowed():
            return self._fail(HTTPStatus.FORBIDDEN, "arthur web only answers localhost")
        if not secrets.compare_digest(self.headers.get("X-Arthur-Token") or "", self.app.token):
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
            if self.path == "/api/actions/break-lock":
                return self._send_json(self.app.break_lock())
            return self._fail(HTTPStatus.NOT_FOUND, "unknown action")
        except KeyError as exc:
            # str(KeyError) wraps the message in quotes
            return self._fail(HTTPStatus.BAD_REQUEST, str(exc.args[0]) if exc.args else str(exc))
        except ValueError as exc:
            return self._fail(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:  # pragma: no cover - last-resort guard
            traceback.print_exc()
            return self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error — see the arthur web terminal")


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
    print("read-only for agents; humans get five actions. Ctrl+C stops it.")
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
