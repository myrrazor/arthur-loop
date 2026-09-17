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

from arthur_loop.browser_lock import BrowserLockError, break_lock, read_lock
from arthur_loop.config import load_config, require_instance
from arthur_loop.decisions import answer_decision as _answer_decision
from arthur_loop.decisions import list_decisions
from arthur_loop.follow import follow_loop, preview_next_step
from arthur_loop.loop_ops import apply_role_updates, create_loop, wizard_options
from arthur_loop.pathguard import READABLE_SUFFIXES, resolve_instance_file
from arthur_loop.roles import formulate_default_loop, resolve_roles, roles_payload
from arthur_loop.queue_ledger import QueueJob, QueueLedger, read_jsonl
from arthur_loop.recovery import recover_job
from arthur_loop.status import clear_session, collect_status, status_to_dict


DEFAULT_PORT = 7433

# routes that never need the session token: the bootstrap page (which itself
# requires ?token=) and the static assets it loads
TOKEN_QUERY = "token"

# static files we are willing to serve, nothing else
STATIC_FILES = {
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}

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
        require_instance(root)
        self.root = root
        self.config = load_config(root)
        # one secret per process: the operator gets it in the URL `arthur web` prints,
        # and every read or write over the API must present it
        self.token = secrets.token_urlsafe(24)
        # ThreadingHTTPServer: writes are read-modify-write, serialize them
        self._write_lock = threading.Lock()

    def token_ok(self, presented: str | None) -> bool:
        return bool(presented) and secrets.compare_digest(presented or "", self.token)

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
            "tracker": self.config["tracker"]["adapter"],
            "roles": resolve_roles(self.config),
        }
        payload["roles"] = roles_payload(self.config)
        payload["loopSequence"] = formulate_default_loop(self.config).get("hops") or []
        payload["nextRun"] = preview_next_step(self.root)
        payload["loopWizard"] = wizard_options(self.root)
        return payload

    def _decision_bodies(self) -> dict[str, str]:
        """Map open-decision titles to their question text (status line stripped)."""

        return {row["title"]: row["body"] for row in list_decisions(self.root)}

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

        candidate = resolve_instance_file(self.root, rel_path, suffixes=READABLE_SUFFIXES)
        size = candidate.stat().st_size
        if size <= MAX_VIEW_BYTES:
            return candidate.read_text(encoding="utf-8")
        with candidate.open("rb") as fh:
            fh.seek(size - MAX_VIEW_BYTES)
            tail = fh.read().decode("utf-8", errors="replace")
        return f"… (file is {size:,} bytes; showing the last {MAX_VIEW_BYTES:,})\n{tail}"

    # ---------------------------------------------------------------- actions

    def answer_decision(self, title: str, answer: str) -> dict[str, Any]:
        """Mark one open human decision answered — same code path as `arthur decision answer`."""

        if not title.strip() or not answer.strip():
            raise ValueError("both a decision title and an answer are required")
        with self._write_lock:
            return _answer_decision(self.root, title, answer)

    def recover_job(self, job_id: str, requeue: bool) -> dict[str, Any]:
        """Park/requeue a job and free its dead manager's lease — same as `arthur queue recover`."""

        with self._write_lock:
            try:
                result = recover_job(self.root, job_id, requeue=requeue, error="parked from the web console")
            except KeyError as exc:
                raise ValueError(str(exc.args[0]) if exc.args else str(exc))
        return result.to_record()

    def create_job(self, body: dict[str, Any]) -> dict[str, Any]:
        required = ["job_id", "project_id", "target_chat_title", "target_chat_url"]
        missing = [
            key for key in required
            if not isinstance(body.get(key), str) or not body[key].strip()
        ]
        if missing:
            raise ValueError(f"missing fields: {', '.join(missing)}")

        job = QueueJob(
            job_id=body["job_id"].strip(),
            project_id=body["project_id"].strip(),
            target_chat_title=body["target_chat_title"].strip(),
            target_chat_url=body["target_chat_url"].strip(),
            prompt_path=str(body.get("prompt_path") or "").strip() or None,
            expected_marker=str(body.get("expected_marker") or "").strip() or None,
            idempotency_key=str(body.get("idempotency_key") or "").strip() or None,
        )
        with self._write_lock:
            # same dedupe rules as the CLI: duplicate id, reused key, colliding marker
            QueueLedger(self.root).create_job(job)
        return job.to_record()

    def create_loop(self, body: dict[str, Any]) -> dict[str, Any]:
        """Wizard path: project + optional first job. Not a graph composer."""

        project_id = str(body.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        seed = body.get("seed_job")
        if seed is None:
            seed = True
        roles = body.get("roles") if isinstance(body.get("roles"), dict) else None
        with self._write_lock:
            record = create_loop(
                self.root,
                project_id=project_id,
                advisor=str(body["advisor"]).strip() if body.get("advisor") else None,
                executor=str(body["executor"]).strip() if body.get("executor") else None,
                tracker=str(body["tracker"]).strip() if body.get("tracker") else None,
                roles=roles,
                title=str(body.get("title") or "").strip() or None,
                target_chat_url=str(body.get("target_chat_url") or "manual").strip() or "manual",
                goal=str(body.get("goal") or ""),
                seed_job=bool(seed),
                actor="web-console",
                reason="create-loop wizard",
                ticket=str(body.get("ticket") or "").strip() or None,
            )
            self.config = load_config(self.root)
        return record

    def set_roles(self, body: dict[str, Any]) -> dict[str, Any]:
        roles = body.get("roles")
        if not isinstance(roles, dict) or not roles:
            raise ValueError("roles object is required")
        with self._write_lock:
            apply_role_updates(self.root, roles=roles)
            self.config = load_config(self.root)
        return roles_payload(self.config)

    def run_next(self, body: dict[str, Any]) -> dict[str, Any]:
        once = body.get("once")
        if once is None:
            once = True
        # follow_loop may spend minutes inside an agent CLI. Its queue and
        # browser-lease writes have their own cross-process locks, so do not
        # block unrelated web mutations on the process-wide write lock.
        return follow_loop(
            self.root,
            once=bool(once),
            max_steps=int(body.get("max_steps") or 12),
            project_id=str(body["project_id"]).strip() if body.get("project_id") else None,
            holder=f"web-run-next-{secrets.token_hex(8)}",
            chain=body.get("chain", True),
            dry_run=bool(body.get("dry_run")),
        )

    def clear_session_action(self, session_id: str) -> dict[str, Any]:
        cleared = clear_session(self.root, session_id)
        return {"session_id": session_id, "cleared": cleared}

    def break_lock(self) -> dict[str, Any]:
        """Remove a STALE browser lock. A fresh lock means the holder is active: refuse."""

        with self._write_lock:
            if read_lock(self.root) is None:
                return {"broken": False, "reason": "no lock held"}
            try:
                broken = break_lock(self.root, force=False, via="web-console")
            except BrowserLockError as exc:
                raise ValueError(str(exc))
        return {"broken": broken is not None, "holder": broken.holder if broken else None}


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

    def _presented_token(self, query: dict[str, list[str]]) -> str | None:
        header = self.headers.get("X-Arthur-Token")
        if header:
            return header
        values = query.get(TOKEN_QUERY)
        return values[0] if values else None

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if not self._host_allowed():
            return self._fail(HTTPStatus.FORBIDDEN, "arthur web only answers localhost")

        url = urlparse(self.path)
        query = parse_qs(url.query)

        try:
            if url.path.startswith("/assets/"):
                name = url.path[len("/assets/"):]
                if name not in STATIC_FILES:
                    return self._fail(HTTPStatus.NOT_FOUND, "unknown asset")
                return self._send_text(_read_ui(name), STATIC_FILES[name])
            if not self.app.token_ok(self._presented_token(query)):
                if url.path == "/":
                    return self._send_text(
                        "Arthur Loop web console: open the exact URL printed by `arthur web` "
                        "(it carries this session's token).\n",
                        "text/plain; charset=utf-8",
                        status=HTTPStatus.FORBIDDEN,
                    )
                return self._fail(HTTPStatus.FORBIDDEN, "missing or wrong session token")
            if url.path == "/":
                html = _read_ui("index.html").replace("__ARTHUR_TOKEN__", self.app.token)
                return self._send_text(html, "text/html; charset=utf-8")
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
        if not self.app.token_ok(self.headers.get("X-Arthur-Token")):
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
            if self.path == "/api/actions/create-loop":
                return self._send_json(self.app.create_loop(body))
            if self.path == "/api/actions/set-roles":
                return self._send_json(self.app.set_roles(body))
            if self.path == "/api/actions/run-next":
                return self._send_json(self.app.run_next(body))
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


def console_url(server: ThreadingHTTPServer) -> str:
    """The one URL that opens the console: it carries this process's session token."""

    app = server.arthur_app  # type: ignore[attr-defined]
    return f"http://127.0.0.1:{server.server_address[1]}/?{TOKEN_QUERY}={app.token}"


def cmd_web(args: Any) -> int:
    root = Path(getattr(args, "root", None) or ".").resolve()
    try:
        server = make_server(root, port=args.port)
    except OSError as exc:
        print(f"error: could not bind 127.0.0.1:{args.port} ({exc})", file=sys.stderr)
        return 2

    url = console_url(server)
    print(f"arthur web console: {url}")
    print(f"instance: {root}")
    print("localhost only; the token in the URL is this session's key — other local users cannot read or write without it.")
    print("Assign roles, run the next hop, create a loop, and answer decisions. Ctrl+C stops it.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nweb console stopped")
    finally:
        server.server_close()
    return 0


def add_web_parser(subparsers: Any, add_root: Any = None) -> None:
    web = subparsers.add_parser(
        "web",
        help="Serve the local web console for this instance (URL with session token is printed)",
    )
    if add_root is not None:
        add_root(web)
    else:
        web.add_argument("--root", default=".", help="Arthur Loop instance root")
    web.add_argument("--port", type=int, default=DEFAULT_PORT)
    web.add_argument("--no-open", action="store_true", help="Do not open the browser")
    web.set_defaults(func=cmd_web)
