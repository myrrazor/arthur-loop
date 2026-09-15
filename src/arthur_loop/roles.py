"""Assignable loop roles: planner, implementer, reviewer, qa.

Legacy config still has advisor (plans + reviews) and executor (implements).
When `roles` is missing, those two fields fill planner/reviewer and implementer.
QA is optional — `none` skips the QA hop.
"""

from __future__ import annotations

from typing import Any


LOOP_ROLES = ("planner", "implementer", "reviewer", "qa")

ROLE_AGENTS: dict[str, frozenset[str]] = {
    "planner": frozenset({"chatgpt-browser", "claude-code", "codex", "grok", "manual"}),
    "implementer": frozenset({"claude-code", "codex", "grok", "manual"}),
    "reviewer": frozenset({"chatgpt-browser", "claude-code", "codex", "grok", "manual"}),
    "qa": frozenset({"chatgpt-browser", "claude-code", "codex", "grok", "manual", "none"}),
}

DEFAULT_ROLES: dict[str, dict[str, str]] = {
    "planner": {"agent": "chatgpt-browser", "model": ""},
    "implementer": {"agent": "codex", "model": ""},
    "reviewer": {"agent": "chatgpt-browser", "model": ""},
    "qa": {"agent": "none", "model": ""},
}

ROLE_FOR_KIND = {
    "next-plan-request": "planner",
    "plan": "implementer",
    "plan-review": "reviewer",
    "implementation-handoff": "implementer",
    "qa-review": "qa",
    "sprint-review": "reviewer",
}

DEFAULT_HOP_KINDS = (
    "next-plan-request",
    "plan",
    "plan-review",
    "implementation-handoff",
    "qa-review",
    "sprint-review",
)

_NEXT_HOP: dict[tuple[str, str], str | None] = {
    ("next-plan-request", "REQUEST_CODEX_PLAN"): "plan",
    ("plan", "READY_FOR_CHATGPT_REVIEW"): "plan-review",
    ("plan-review", "APPROVE_PLAN"): "implementation-handoff",
    ("plan-review", "REVISE_PLAN"): "plan",
    ("qa-review", "QA_PASS"): "sprint-review",
    ("qa-review", "QA_FAIL"): "implementation-handoff",
    ("sprint-review", "FIX_REQUIRED"): "implementation-handoff",
    ("sprint-review", "APPROVE_SPRINT"): "next-plan-request",
}


def empty_assignment(agent: str = "", model: str = "") -> dict[str, str]:
    return {"agent": str(agent or ""), "model": str(model or "")}


def role_assigned(assignment: dict[str, Any] | None) -> bool:
    agent = str((assignment or {}).get("agent") or "").strip()
    return bool(agent) and agent != "none"


def derive_roles(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Fill roles from advisor/executor when an instance has no roles block."""

    advisor = str((config.get("advisor") or {}).get("adapter") or DEFAULT_ROLES["planner"]["agent"])
    executor = str((config.get("executor") or {}).get("adapter") or DEFAULT_ROLES["implementer"]["agent"])
    advisor_model = str((config.get("advisor") or {}).get("model") or "")
    executor_model = str((config.get("executor") or {}).get("model") or "")
    return {
        "planner": empty_assignment(advisor, advisor_model),
        "implementer": empty_assignment(executor, executor_model),
        "reviewer": empty_assignment(advisor, advisor_model),
        "qa": empty_assignment("none", ""),
    }


def merge_roles(
    base: dict[str, Any] | None,
    overlay: dict[str, Any] | None,
) -> dict[str, dict[str, str]]:
    merged = {role: dict(DEFAULT_ROLES[role]) for role in LOOP_ROLES}
    for source in (base, overlay):
        if not isinstance(source, dict):
            continue
        for role in LOOP_ROLES:
            value = source.get(role)
            if not isinstance(value, dict):
                continue
            merged[role] = {
                "agent": str(value.get("agent") or merged[role]["agent"]),
                "model": str(value.get("model") if value.get("model") is not None else merged[role]["model"]),
            }
    return merged


def validate_roles(roles: dict[str, Any]) -> None:
    for role in LOOP_ROLES:
        assignment = roles.get(role) or {}
        agent = str(assignment.get("agent") or "")
        known = ROLE_AGENTS[role]
        if agent not in known:
            raise ValueError(f"unknown {role} agent {agent!r} — expected one of {sorted(known)}")


def resolve_roles(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    roles = merge_roles(derive_roles(config), config.get("roles") if isinstance(config.get("roles"), dict) else None)
    validate_roles(roles)
    return roles


def assignment_for(config: dict[str, Any], role: str) -> dict[str, str]:
    if role in {"advisor", "executor"}:
        role = "planner" if role == "advisor" else "implementer"
    roles = resolve_roles(config)
    if role not in roles:
        raise ValueError(f"unknown role {role!r} — expected one of {list(LOOP_ROLES)}")
    return dict(roles[role])


def role_for_kind(kind: str) -> str:
    return ROLE_FOR_KIND.get(kind, "planner")


def parse_role_spec(spec: str) -> dict[str, str]:
    """Parse `claude-code` or `claude-code:opus`."""

    raw = (spec or "").strip()
    if not raw:
        raise ValueError("role assignment is empty")
    agent, sep, model = raw.partition(":")
    agent = agent.strip()
    if not agent:
        raise ValueError("role assignment needs an agent")
    return empty_assignment(agent, model.strip() if sep else "")


def parse_role_updates(items: list[str]) -> dict[str, dict[str, str]]:
    """Parse CLI tokens like `reviewer=claude-code:opus`."""

    updates: dict[str, dict[str, str]] = {}
    for item in items:
        role, sep, spec = item.partition("=")
        role = role.strip()
        if not sep or role not in LOOP_ROLES:
            raise ValueError(f"expected role=agent[:model] with role in {list(LOOP_ROLES)}, got {item!r}")
        updates[role] = parse_role_spec(spec)
    return updates


def next_hop_kind(kind: str, decision: str, config: dict[str, Any] | None = None) -> str | None:
    """Return the next hop, inserting QA after implementation when that role is assigned."""

    if kind == "implementation-handoff" and decision == "COMPLETE":
        roles = resolve_roles(config or {})
        if role_assigned(roles.get("qa")):
            return "qa-review"
        return "sprint-review"
    return _NEXT_HOP.get((kind, decision))


def formulate_default_loop(
    config: dict[str, Any],
    *,
    project_id: str = "",
    ticket: str | None = None,
    goal: str = "",
) -> dict[str, Any]:
    """Default hop sequence for a ticket or project. Not a graph composer."""

    roles = resolve_roles(config)
    hops: list[dict[str, Any]] = []
    for kind in DEFAULT_HOP_KINDS:
        role = ROLE_FOR_KIND[kind]
        if kind == "qa-review" and not role_assigned(roles.get("qa")):
            continue
        assignment = roles[role]
        hops.append(
            {
                "kind": kind,
                "role": role,
                "agent": assignment["agent"],
                "model": assignment.get("model") or "",
            }
        )
    return {
        "source": "default",
        "project_id": project_id,
        "ticket": ticket or None,
        "goal": goal or "",
        "hops": hops,
        "honest_copy": (
            "Default loop: planner scopes, implementer plans, reviewer approves, "
            "implementer builds"
            + (", QA checks" if role_assigned(roles.get("qa")) else "")
            + ", reviewer closes the sprint. Arthur hands each hop to the assigned agent."
        ),
    }


def roles_payload(config: dict[str, Any]) -> dict[str, Any]:
    roles = resolve_roles(config)
    return {
        "roles": roles,
        "agents": {role: sorted(ROLE_AGENTS[role]) for role in LOOP_ROLES},
        "advisor": (config.get("advisor") or {}).get("adapter"),
        "executor": (config.get("executor") or {}).get("adapter"),
        "sequence": formulate_default_loop(config)["hops"],
    }
