from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .capabilities import VerificationLevel, verification_rank
from .errors import CorruptArchive, UnsupportedFormat
from .safety import validate_members


@dataclass(slots=True)
class VerificationCheck:
    name: str
    status: str
    provider: str
    detail: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "status": self.status, "provider": self.provider, "detail": self.detail}


@dataclass(slots=True)
class VerificationEvidence:
    requested: VerificationLevel
    achieved: VerificationLevel
    status: str
    backend: str | None = None
    downgraded: bool = False
    reason: str | None = None
    checks: list[VerificationCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"passed", "skipped"}

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "requested": self.requested.value,
            "achieved": self.achieved.value,
            "status": self.status,
            "backend": self.backend,
            "downgraded": self.downgraded,
            "reason": self.reason,
            "checks": [check.to_dict() for check in self.checks],
        }


def choose_level(requested: VerificationLevel, supported: set[VerificationLevel], *, allow_downgrade: bool) -> tuple[VerificationLevel, bool, str | None]:
    if requested in supported:
        return requested, False, None
    stronger = [level for level in supported if verification_rank(level) > verification_rank(requested)]
    if stronger:
        chosen = min(stronger, key=verification_rank)
        return chosen, False, f"backend proof {chosen.value} is stronger than requested {requested.value}"
    weaker = [level for level in supported if verification_rank(level) < verification_rank(requested)]
    if allow_downgrade and weaker:
        chosen = max(weaker, key=verification_rank)
        return chosen, True, f"requested {requested.value}; backend maximum is {chosen.value}"
    available = ", ".join(level.value for level in sorted(supported, key=verification_rank)) or "none"
    raise UnsupportedFormat(
        f"verification level {requested.value} is unavailable; supported levels: {available}; "
        "use --allow-verification-downgrade to accept a weaker proof"
    )


def verify_with_backend(
    *,
    path: Path,
    backend,
    requested: VerificationLevel,
    allow_downgrade: bool,
    list_members: Callable[[], list] | None,
    run_full: Callable[[], tuple[bool, str | None]],
) -> VerificationEvidence:
    profile = getattr(backend.info, "capability_profile", None)
    if profile is not None:
        supported = set(profile.verification_levels)
    else:
        caps = set(getattr(backend.info, "capabilities", set()) or set())
        supported = {VerificationLevel.NONE}
        if "test" in caps:
            supported.update({VerificationLevel.STRUCTURE, VerificationLevel.FULL})
        if "safe-index" in caps or "list" in caps:
            supported.update({VerificationLevel.STRUCTURE, VerificationLevel.MEMBERS})
    chosen, downgraded, reason = choose_level(requested, supported, allow_downgrade=allow_downgrade)
    provider = backend.info.binary
    evidence = VerificationEvidence(requested, chosen, "passed", backend=provider, downgraded=downgraded, reason=reason)
    if chosen is VerificationLevel.NONE:
        evidence.status = "skipped"
        evidence.checks.append(VerificationCheck("verification", "skipped", provider, "verification explicitly disabled"))
        return evidence

    if chosen in {VerificationLevel.STRUCTURE, VerificationLevel.MEMBERS}:
        if list_members is None:
            # Streams have no member index. Their native test is stronger than
            # structure and is therefore a truthful way to satisfy structure.
            ok, detail = run_full()
            if not ok:
                evidence.status = "failed"
                evidence.checks.append(VerificationCheck("stream-integrity", "failed", provider, detail))
                return evidence
            evidence.achieved = VerificationLevel.FULL
            evidence.checks.append(VerificationCheck("stream-integrity", "passed", provider, "native stream integrity test"))
            return evidence
        try:
            members = list_members()
            evidence.checks.append(VerificationCheck("container-index", "passed", provider, f"{len(members)} member(s) indexed"))
            if chosen is VerificationLevel.MEMBERS:
                validate_members(members)
                evidence.checks.append(VerificationCheck("member-safety", "passed", "arc", "normalized member index validated"))
            return evidence
        except Exception as exc:
            evidence.status = "failed"
            evidence.checks.append(VerificationCheck("container-index", "failed", provider, str(exc)))
            return evidence

    ok, detail = run_full()
    if ok:
        evidence.checks.append(VerificationCheck("full-content", "passed", provider, "backend integrity test consumed archive data"))
    else:
        evidence.status = "failed"
        evidence.checks.append(VerificationCheck("full-content", "failed", provider, detail))
    return evidence
