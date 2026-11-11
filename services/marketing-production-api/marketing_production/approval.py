"""Approval gate (HUMAN-GATE): a person's decision bound to one package's sha256.

``record_approval`` writes ``<package stem>.approval.json`` next to the package with the
sha256 of the package and of every export in its manifest (the bulk CSV)
(``campaign-approval.v1``). ``approval_status`` returns ``pending_review`` when
no record exists, the recorded decision while the package and every export still match,
and ``stale`` once any of them changed or went missing. Approval never publishes, schedules, sends,
or spends anything; it only tells the person who publishes by hand that this
exact package was reviewed.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DECISIONS = ("approved", "changes_requested", "rejected")
SCHEMA = "campaign-approval.v1"
_REVIEWER = re.compile(r"^[\w .@-]{1,80}$")


def approval_path(package_path: str | Path) -> Path:
    path = Path(package_path)
    return path.with_name(path.name.removesuffix(".campaign.json") + ".approval.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_exports(package: Path) -> list[dict[str, str]]:
    """Exports listed in the accepted manifest that names this package (``filename``, manifest ``sha256``)."""

    for manifest_path in sorted(package.parent.glob("manifest-*.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict) or manifest.get("package", {}).get("filename") != package.name:
            continue
        exports = []
        for item in manifest.get("exports", []) or []:
            name = str(item.get("filename", "")) if isinstance(item, dict) else ""
            if not name or Path(name).name != name:
                raise ValueError("manifest_export_invalid")
            exports.append({"filename": name, "sha256": str(item.get("content_hash", ""))})
        return exports
    # No manifest next to the package: cover the sibling bulk CSV if it exists.
    sibling = package.with_name(package.name.removesuffix(".campaign.json") + ".bulk.csv")
    return [{"filename": sibling.name, "sha256": _sha256(sibling)}] if sibling.is_file() else []


def record_approval(
    package_path: str | Path,
    *,
    decision: str,
    reviewer: str,
    note: str = "",
    decided_at: str | None = None,
) -> dict[str, Any]:
    """Record a decision for the package as it is now; raises ``ValueError`` with a stable code."""

    package = Path(package_path)
    if decision not in DECISIONS:
        raise ValueError("invalid_decision")
    if not _REVIEWER.fullmatch(reviewer):
        raise ValueError("invalid_reviewer")
    if len(note) > 2_000:
        raise ValueError("note_too_long")
    if not package.is_file() or not package.name.endswith(".campaign.json"):
        raise ValueError("package_missing")
    exports = []
    for item in _manifest_exports(package):
        path = package.parent / item["filename"]
        if not path.is_file():
            raise ValueError("export_missing")
        current = _sha256(path)
        if current != item["sha256"]:
            raise ValueError("export_hash_mismatch")  # changed after the build: rebuild before approving
        exports.append({"filename": item["filename"], "sha256": current})
    record = {
        "schema_version": SCHEMA,
        "package": {"filename": package.name, "sha256": _sha256(package)},
        "exports": exports,
        "decision": decision,
        "reviewer": reviewer,
        "note": note,
        "decided_at": decided_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "publishes": False,
    }
    approval_path(package).write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def approval_status(package_path: str | Path) -> dict[str, Any]:
    package = Path(package_path)
    if not package.is_file():
        raise ValueError("package_missing")
    record_file = approval_path(package)
    if not record_file.is_file():
        return {"status": "pending_review", "package": package.name}
    try:
        record = json.loads(record_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "stale", "package": package.name, "reason": "approval_unreadable"}
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA or record.get("decision") not in DECISIONS:
        return {"status": "stale", "package": package.name, "reason": "approval_schema_invalid"}
    if record.get("package", {}).get("sha256") != _sha256(package):
        return {"status": "stale", "package": package.name, "reason": "package_changed"}
    recorded = record.get("exports")
    if not isinstance(recorded, list):
        return {"status": "stale", "package": package.name, "reason": "approval_schema_invalid"}
    try:
        expected = {item["filename"] for item in _manifest_exports(package)}
    except ValueError:
        return {"status": "stale", "package": package.name, "reason": "manifest_export_invalid"}
    covered = {str(item.get("filename")) for item in recorded if isinstance(item, dict)}
    if not expected <= covered:
        return {"status": "stale", "package": package.name, "reason": "export_not_covered"}
    for item in recorded:
        name = str(item.get("filename", "")) if isinstance(item, dict) else ""
        path = package.parent / name
        if not name or Path(name).name != name or not path.is_file():
            return {"status": "stale", "package": package.name, "reason": "export_missing"}
        if _sha256(path) != item.get("sha256"):
            return {"status": "stale", "package": package.name, "reason": "export_changed"}
    return {"status": record["decision"], "package": package.name, "reviewer": record.get("reviewer"), "decided_at": record.get("decided_at")}


__all__ = ["DECISIONS", "approval_path", "approval_status", "record_approval"]
