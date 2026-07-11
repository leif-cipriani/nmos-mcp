"""MCP-enforced authorization for NMOS write actions.

Enforcement is intentionally *in code* (invoked from the service layer before any
HTTP call), so it cannot be bypassed by prompt injection or a misbehaving LLM.

Posture: reads/queries are always allowed. Every write action (connect, disconnect,
enable, disable, stage) must be explicitly granted by an allow rule whose scope
matches the target resource; explicit ``deny`` rules override allows.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .errors import NmosError, PermissionDeniedError

logger = logging.getLogger("nmos_mcp.permissions")

# --- Action taxonomy ---------------------------------------------------------
READ = "read"
CONNECT = "connect"
DISCONNECT = "disconnect"
ENABLE = "enable"
DISABLE = "disable"
STAGE = "stage"
WRITE = "write"  # coarse verb: any of the write actions below

WRITE_ACTIONS: frozenset[str] = frozenset({CONNECT, DISCONNECT, ENABLE, DISABLE, STAGE})
ALL_ACTIONS: frozenset[str] = WRITE_ACTIONS | {READ}

ALL_GROUP = "all"  # built-in group matching every resource

# Cache device/node metadata briefly; topology/tags rarely change mid-session.
_META_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class ResourceRef:
    kind: str  # "senders" | "receivers"
    id: str


@dataclass
class TargetMeta:
    """The metadata a policy is evaluated against for one resource."""

    ref: ResourceRef
    device_id: str | None = None
    node_id: str | None = None
    node_label: str | None = None
    labels: set[str] = field(default_factory=set)
    tags: dict[str, list[str]] = field(default_factory=dict)

    def describe(self) -> str:
        parts = [f"{self.ref.kind[:-1]} {self.ref.id}"]
        if self.device_id:
            parts.append(f"device {self.device_id}")
        if self.node_label or self.node_id:
            parts.append(f"node {self.node_label or self.node_id}")
        return ", ".join(parts)


# --- Group + selector matching ----------------------------------------------
@dataclass
class Group:
    name: str
    devices: set[str] = field(default_factory=set)
    nodes: set[str] = field(default_factory=set)  # node ids or labels
    labels: list[re.Pattern] = field(default_factory=list)  # compiled regexes
    tags: dict[str, set[str]] = field(default_factory=dict)  # key -> allowed values

    def matches(self, meta: TargetMeta) -> bool:
        if self.name == ALL_GROUP:
            return True
        # Any selector category matching puts the target in the group (union).
        if self.devices and meta.device_id and meta.device_id in self.devices:
            return True
        if self.nodes and (
            (meta.node_id and meta.node_id in self.nodes)
            or (meta.node_label and meta.node_label in self.nodes)
        ):
            return True
        if self.labels and any(p.search(lbl) for p in self.labels for lbl in meta.labels):
            return True
        if self.tags:
            for key, wanted in self.tags.items():
                if wanted.intersection(meta.tags.get(key, [])):
                    return True
        return False

    @classmethod
    def parse(cls, name: str, spec: dict[str, Any]) -> "Group":
        try:
            labels = [re.compile(p) for p in spec.get("labels", [])]
        except re.error as exc:
            raise NmosError(f"Invalid label regex in group '{name}': {exc}") from exc
        raw_tags = spec.get("tags", {}) or {}
        tags = {k: set(v if isinstance(v, list) else [v]) for k, v in raw_tags.items()}
        return cls(
            name=name,
            devices=set(spec.get("devices", []) or []),
            nodes=set(spec.get("nodes", []) or []),
            labels=labels,
            tags=tags,
        )


@dataclass
class Rule:
    actions: set[str]
    groups: list[str]  # group names; empty or ["all"] => every resource

    def grants(self, action: str) -> bool:
        if action in self.actions:
            return True
        return action in WRITE_ACTIONS and WRITE in self.actions

    def scope_matches(self, meta: TargetMeta, groups: dict[str, Group]) -> bool:
        if not self.groups or ALL_GROUP in self.groups:
            return True
        return any(groups[g].matches(meta) for g in self.groups if g in groups)

    @classmethod
    def parse(cls, spec: dict[str, Any], known_groups: set[str]) -> "Rule":
        actions = spec.get("actions") or []
        if isinstance(actions, str):
            actions = [actions]
        actions_set = {a.strip().lower() for a in actions}
        valid = ALL_ACTIONS | {WRITE}
        unknown = actions_set - valid
        if unknown:
            raise NmosError(f"Unknown action(s) in rule: {sorted(unknown)}. Valid: {sorted(valid)}.")
        groups = spec.get("groups") or []
        if isinstance(groups, str):
            groups = [groups]
        for g in groups:
            if g != ALL_GROUP and g not in known_groups:
                raise NmosError(f"Rule references undefined group '{g}'.")
        return cls(actions=actions_set, groups=list(groups))


@dataclass
class Policy:
    groups: dict[str, Group] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)
    source: str = "(none)"

    @classmethod
    def parse(cls, data: dict[str, Any] | None, source: str) -> "Policy":
        data = data or {}
        groups = {name: Group.parse(name, spec or {}) for name, spec in (data.get("groups") or {}).items()}
        known = set(groups) | {ALL_GROUP}
        rules = [Rule.parse(r, known) for r in (data.get("rules") or [])]
        deny = [Rule.parse(r, known) for r in (data.get("deny") or [])]
        return cls(groups=groups, rules=rules, deny=deny, source=source)

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "groups": {
                name: {
                    "devices": sorted(g.devices),
                    "nodes": sorted(g.nodes),
                    "labels": [p.pattern for p in g.labels],
                    "tags": {k: sorted(v) for k, v in g.tags.items()},
                }
                for name, g in self.groups.items()
            },
            "rules": [{"actions": sorted(r.actions), "groups": r.groups or [ALL_GROUP]} for r in self.rules],
            "deny": [{"actions": sorted(r.actions), "groups": r.groups or [ALL_GROUP]} for r in self.deny],
        }


def load_policy(path: str | None) -> Policy:
    """Load a YAML/JSON policy file (YAML is a JSON superset). None => empty policy."""
    if not path:
        return Policy(source="(no policy file — all writes denied in enforce mode)")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise NmosError(f"Could not read permissions file '{path}': {exc}") from exc
    try:
        import yaml

        data = yaml.safe_load(text)
    except ImportError:  # pragma: no cover - pyyaml is a declared dependency
        data = json.loads(text)
    except Exception as exc:  # malformed YAML
        raise NmosError(f"Could not parse permissions file '{path}': {exc}") from exc
    if data is not None and not isinstance(data, dict):
        raise NmosError(f"Permissions file '{path}' must contain a mapping at the top level.")
    return Policy.parse(data, source=path)


# --- Metadata resolution (uses ungated registry reads) -----------------------
class MetadataResolver:
    def __init__(self, query: Any) -> None:
        self._query = query
        self._cache: dict[ResourceRef, tuple[float, TargetMeta]] = {}

    async def resolve(self, ref: ResourceRef) -> TargetMeta:
        now = time.monotonic()
        cached = self._cache.get(ref)
        if cached and now - cached[0] < _META_TTL_SECONDS:
            return cached[1]

        meta = TargetMeta(ref=ref)
        resource = await self._query.get_resource(ref.kind, ref.id)
        _add_label(meta, resource.get("label"))
        _merge_tags(meta, resource.get("tags"))

        device_id = resource.get("device_id")
        if device_id:
            meta.device_id = device_id
            device = await self._query.get_resource("devices", device_id)
            _add_label(meta, device.get("label"))
            _merge_tags(meta, device.get("tags"))
            node_id = device.get("node_id")
            if node_id:
                meta.node_id = node_id
                try:
                    node = await self._query.get_resource("nodes", node_id)
                    meta.node_label = node.get("label")
                    _add_label(meta, node.get("label"))
                    _merge_tags(meta, node.get("tags"))
                except NmosError:
                    pass  # node metadata is best-effort for label/tag matching

        self._cache[ref] = (now, meta)
        return meta


def _add_label(meta: TargetMeta, label: str | None) -> None:
    if label:
        meta.labels.add(label)


def _merge_tags(meta: TargetMeta, tags: dict[str, Any] | None) -> None:
    for key, values in (tags or {}).items():
        vals = values if isinstance(values, list) else [values]
        meta.tags.setdefault(key, [])
        for v in vals:
            if v not in meta.tags[key]:
                meta.tags[key].append(v)


# --- The engine --------------------------------------------------------------
@dataclass
class Decision:
    allowed: bool
    reason: str


class PolicyEngine:
    def __init__(self, policy: Policy, resolver: MetadataResolver, mode: str = "enforce") -> None:
        self._policy = policy
        self._resolver = resolver
        self._mode = mode

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def policy(self) -> Policy:
        return self._policy

    def _decide(self, action: str, meta: TargetMeta) -> Decision:
        if self._mode == "open":
            return Decision(True, "permissions disabled (mode=open)")
        if action == READ:
            return Decision(True, "reads are always allowed")

        # Deny-override: an explicit matching deny rule wins.
        for rule in self._policy.deny:
            if rule.grants(action) and rule.scope_matches(meta, self._policy.groups):
                return Decision(False, f"blocked by an explicit deny rule for '{action}'")
        for rule in self._policy.rules:
            if rule.grants(action) and rule.scope_matches(meta, self._policy.groups):
                return Decision(True, f"allowed by rule granting '{action}' (groups={rule.groups or [ALL_GROUP]})")
        return Decision(
            False,
            f"no allow rule grants '{action}' for this target in policy '{self._policy.source}'",
        )

    async def require(self, action: str, refs: list[ResourceRef]) -> None:
        """Raise :class:`PermissionDeniedError` if *any* ref is not permitted.

        Evaluated (and thus able to reject) before the caller performs any HTTP write.
        """
        if self._mode == "open":
            return
        for ref in refs:
            meta = await self._resolver.resolve(ref)
            decision = self._decide(action, meta)
            logger.info(
                "AUDIT %s action=%s target=%s reason=%s",
                "ALLOW" if decision.allowed else "DENY",
                action,
                meta.describe(),
                decision.reason,
            )
            if not decision.allowed:
                raise PermissionDeniedError(
                    f"Permission denied: '{action}' on {meta.describe()} — {decision.reason}."
                )


def build_engine(settings: Any, query: Any) -> PolicyEngine:
    """Construct a :class:`PolicyEngine` from settings + a QueryClient."""
    policy = load_policy(getattr(settings, "permissions_file", None))
    resolver = MetadataResolver(query)
    return PolicyEngine(policy, resolver, mode=getattr(settings, "permissions_mode", "enforce"))
