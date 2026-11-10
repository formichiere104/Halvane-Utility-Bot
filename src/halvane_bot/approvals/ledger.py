"""The approval ledger and what an approval changes. Pure: no network, no Discord.

Ledger value stored in the game's ApprovalLedger data store, key = Roblox user id:
    {
      "v": 1,
      "leadership": {"forklift_operations": "team_lead"},   # highest approved step per division
      "grade": "team_lead",                                   # highest step in any division (convenience)
      "postings": ["dispatch"],                               # postings held (approved or given in the group)
      "certifications": ["reach_truck_certified"],            # same
      "updatedAt": 1794350000,
      "log": [{"at": ..., "by": "discord:<id>", "action": "approve", ...}]   # newest last, 50 kept
    }
The game reads it on join and when a "personnel" message arrives; only the bot and staff tools write it.
Certifications and postings follow the Roblox group (mirror_group), so staff can also give or take them there.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from ..ids import IdRegistry

LEDGER_VERSION = 1
LOG_KEEP = 50
TIERS = ("nco", "officer", "command")


class ApprovalError(Exception):
    """A request that doesn't make sense (unknown division, already at that rank, ...). Shown to staff."""


class ApprovalConfigError(Exception):
    """config/approvals.yaml is malformed or names unknown roles."""


@dataclass(frozen=True)
class Division:
    key: str
    label: str
    posting_group_role: int | None
    tier_roles: dict[str, int]  # tier -> Discord role id


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    grade_group_role: int
    tier: str
    rank: int  # position on the ladder, 0 = lowest


@dataclass(frozen=True)
class Certification:
    key: str
    label: str
    group_role: int


@dataclass(frozen=True)
class ApprovalConfig:
    ledger_store: str
    message_topic: str
    notices_channel: str
    divisions: dict[str, Division]
    steps: dict[str, Step]
    certifications: dict[str, Certification]

    def step_list(self) -> list[Step]:
        return sorted(self.steps.values(), key=lambda s: s.rank)

    @property
    def grade_group_roles(self) -> frozenset[int]:
        return frozenset(s.grade_group_role for s in self.steps.values())


def load_approvals(path: Path, ids: IdRegistry) -> ApprovalConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        divisions = {}
        for key, d in (raw.get("divisions") or {}).items():
            tiers = d.get("tiers") or {}
            if set(tiers) != set(TIERS):
                raise ApprovalConfigError(f"division {key}: tiers must be exactly {TIERS}")
            divisions[key] = Division(key, str(d["label"]),
                                      int(d["posting_group_role"]) if d.get("posting_group_role") else None,
                                      {t: ids.role(k) for t, k in tiers.items()})
        steps = {}
        for i, s in enumerate(raw.get("steps") or []):
            if s.get("tier") not in TIERS:
                raise ApprovalConfigError(f"step {s.get('key')}: tier must be one of {TIERS}")
            steps[str(s["key"])] = Step(str(s["key"]), str(s["label"]), int(s["grade_group_role"]), s["tier"], i)
        certs = {k: Certification(k, str(c["label"]), int(c["group_role"]))
                 for k, c in (raw.get("certifications") or {}).items()}
        return ApprovalConfig(str(raw["ledger_store"]), str(raw["message_topic"]), str(raw["notices_channel"]),
                              divisions, steps, certs)
    except KeyError as exc:
        raise ApprovalConfigError(f"{path.name}: missing {exc}") from None
    except Exception as exc:  # unknown role keys and bad numbers
        if isinstance(exc, ApprovalConfigError):
            raise
        raise ApprovalConfigError(f"{path.name}: {exc}") from None


# ---- the ledger ---------------------------------------------------------------------------------------

@dataclass
class Ledger:
    leadership: dict[str, str] = field(default_factory=dict)
    postings: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_value(cls, value: Any) -> Ledger:
        if not isinstance(value, dict):
            return cls()
        return cls(
            leadership={str(k): str(v) for k, v in (value.get("leadership") or {}).items()},
            postings=[str(p) for p in value.get("postings") or []],
            certifications=[str(c) for c in value.get("certifications") or []],
            log=list(value.get("log") or [])[-LOG_KEEP:],
        )

    def to_value(self, cfg: ApprovalConfig, now: float) -> dict[str, Any]:
        """The data store value. Never contains null (Roblox refuses it): no leadership rank means no "grade"."""
        g = grade(self, cfg)
        value: dict[str, Any] = {"v": LEDGER_VERSION, "leadership": dict(sorted(self.leadership.items()))}
        if g:
            value["grade"] = g.key
        value.update({"postings": sorted(set(self.postings)), "certifications": sorted(set(self.certifications)),
                      "updatedAt": int(now), "log": self.log[-LOG_KEEP:]})
        return value


def _copy(ledger: Ledger) -> Ledger:
    return Ledger(dict(ledger.leadership), list(ledger.postings), list(ledger.certifications), list(ledger.log))


def _division(cfg: ApprovalConfig, key: str) -> Division:
    if key not in cfg.divisions:
        raise ApprovalError(f"Unknown division `{key}`.")
    return cfg.divisions[key]


def _step(cfg: ApprovalConfig, key: str) -> Step:
    if key not in cfg.steps:
        raise ApprovalError(f"Unknown rank `{key}`.")
    return cfg.steps[key]


def approve(ledger: Ledger, cfg: ApprovalConfig, division: str, step: str, by: str, now: float) -> Ledger:
    """step is a ladder key, or "posting" to unlock the division's posting."""
    d = _division(cfg, division)
    new = _copy(ledger)
    if step == "posting":
        if d.posting_group_role is None:
            raise ApprovalError(f"{d.label} is open to everyone; there is no posting to approve.")
        if division in new.postings:
            raise ApprovalError(f"The {d.label} posting is already approved.")
        new.postings.append(division)
    else:
        s = _step(cfg, step)
        if d.posting_group_role is not None and division not in new.postings:
            raise ApprovalError(f"{d.label} needs its posting first: approve it with rank **Posting**, "
                                "then the leadership rank.")
        current = new.leadership.get(division)
        if current in cfg.steps and cfg.steps[current].rank >= s.rank:
            raise ApprovalError(f"Already {cfg.steps[current].label} in {d.label}. Use /demote to lower a rank.")
        new.leadership[division] = s.key
    new.log.append({"at": int(now), "by": by, "action": "approve", "division": division, "step": step})
    return new


def demote(ledger: Ledger, cfg: ApprovalConfig, division: str, to: str | None, by: str, now: float) -> Ledger:
    """Lower (or clear, to=None) the leadership step in one division. Postings and certifications stay."""
    d = _division(cfg, division)
    current = ledger.leadership.get(division)
    if current is None:
        raise ApprovalError(f"No leadership rank recorded in {d.label}.")
    new = _copy(ledger)
    if to is None:
        del new.leadership[division]
    else:
        s = _step(cfg, to)
        if current in cfg.steps and s.rank >= cfg.steps[current].rank:
            raise ApprovalError(f"{s.label} is not below the current {cfg.steps[current].label}.")
        new.leadership[division] = s.key
    new.log.append({"at": int(now), "by": by, "action": "demote", "division": division, "step": to or "none"})
    return new


def certify(ledger: Ledger, cfg: ApprovalConfig, cert: str, by: str, now: float) -> Ledger:
    if cert not in cfg.certifications:
        raise ApprovalError(f"Unknown certification `{cert}`.")
    if cert in ledger.certifications:
        raise ApprovalError(f"{cfg.certifications[cert].label} is already recorded.")
    new = _copy(ledger)
    new.certifications.append(cert)
    new.log.append({"at": int(now), "by": by, "action": "certify", "certification": cert})
    return new


def mirror_group(ledger: Ledger, have: Iterable[int], cfg: ApprovalConfig, now: float) -> Ledger | None:
    """Certifications and postings follow the Roblox group, so staff can give or take them there by hand too.

    `have` is every group role the member holds. Keys the config no longer knows are kept. Leadership is never
    touched: a grade role doesn't say which division it belongs to. Returns the updated ledger, or None when the
    ledger already matches the group."""
    roles = set(have)
    known_posts = {k for k, d in cfg.divisions.items() if d.posting_group_role}
    certs = ({c for c in ledger.certifications if c not in cfg.certifications}
             | {k for k, c in cfg.certifications.items() if c.group_role in roles})
    posts = ({p for p in ledger.postings if p not in known_posts}
             | {k for k in known_posts if cfg.divisions[k].posting_group_role in roles})
    old_certs, old_posts = set(ledger.certifications), set(ledger.postings)
    if certs == old_certs and posts == old_posts:
        return None
    new = _copy(ledger)
    new.certifications, new.postings = sorted(certs), sorted(posts)
    entry: dict[str, Any] = {"at": int(now), "by": "roblox-group", "action": "group"}
    added = [f"certification:{c}" for c in sorted(certs - old_certs)] + [f"posting:{p}" for p in sorted(posts - old_posts)]
    removed = [f"certification:{c}" for c in sorted(old_certs - certs)] + [f"posting:{p}" for p in sorted(old_posts - posts)]
    if added:
        entry["added"] = added
    if removed:
        entry["removed"] = removed
    new.log.append(entry)
    return new


def mirror_labels(old: Ledger, new: Ledger, cfg: ApprovalConfig) -> list[str]:
    """What mirror_group changed, for people: ["+Reach Truck Certified", "-Dispatch posting"]."""
    def cert(k: str) -> str:
        return cfg.certifications[k].label if k in cfg.certifications else k

    def post(k: str) -> str:
        return f"{cfg.divisions[k].label if k in cfg.divisions else k} posting"
    oc, nc, op, np_ = set(old.certifications), set(new.certifications), set(old.postings), set(new.postings)
    return ([f"+{cert(k)}" for k in sorted(nc - oc)] + [f"-{cert(k)}" for k in sorted(oc - nc)]
            + [f"+{post(k)}" for k in sorted(np_ - op)] + [f"-{post(k)}" for k in sorted(op - np_)])


# ---- what the ledger means --------------------------------------------------------------------------

def grade(ledger: Ledger, cfg: ApprovalConfig) -> Step | None:
    steps = [cfg.steps[s] for s in ledger.leadership.values() if s in cfg.steps]
    return max(steps, key=lambda s: s.rank) if steps else None


def tier(ledger: Ledger, cfg: ApprovalConfig, division: str) -> str | None:
    s = ledger.leadership.get(division)
    return cfg.steps[s].tier if s in cfg.steps else None


def group_changes(have: frozenset[int] | set[int], ledger: Ledger, cfg: ApprovalConfig) -> tuple[set[int], set[int]]:
    """(assign, unassign) on the Roblox group. One grade role at a time; postings and certifications are only
    ever added. Roles the ledger doesn't cover are never touched."""
    want: set[int] = set()
    g = grade(ledger, cfg)
    if g:
        want.add(g.grade_group_role)
    for p in ledger.postings:
        role = cfg.divisions[p].posting_group_role if p in cfg.divisions else None
        if role:
            want.add(role)
    for c in ledger.certifications:
        if c in cfg.certifications:
            want.add(cfg.certifications[c].group_role)
    unassign = (set(have) & cfg.grade_group_roles) - want
    return want - set(have), unassign


def tier_changes(have: set[int], ledger: Ledger, cfg: ApprovalConfig, division: str) -> tuple[set[int], set[int]]:
    """(add, remove) Discord tier roles for one division only."""
    d = cfg.divisions[division]
    t = tier(ledger, cfg, division)
    want = {d.tier_roles[t]} if t else set()
    mine = set(d.tier_roles.values())
    return want - have, (have & mine) - want
