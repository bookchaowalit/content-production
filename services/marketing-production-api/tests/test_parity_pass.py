"""Parity pass 2026-11-12 (spec docs/superpowers/specs/2026-11-12-marketing-parity.md, M1-M6)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest
from marketing_production import jobs
from marketing_production.approval import approval_status, record_approval
from marketing_production.channel_tools import BULK_COLUMNS, sample_size_per_variant
from marketing_production.cli import main as marketing_cli
from marketing_production.copy_rules import forbidden_hits
from marketing_production.jobs import run_campaign_job


def _payload(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "campaign_id": "p-01",
        "name": "Parity",
        "objective": "conversion",
        "channels": ["email", "instagram"],
        "audience": {"name": "members", "rules": [{"field": "language", "operator": "equals", "value": "th"}]},
        "assets": [
            {"asset_id": "ig-a", "kind": "copy", "channel": "instagram", "uri": "inline:a", "text": "ลองเลย #sale"},
            {"asset_id": "mail", "kind": "email", "channel": "email", "uri": "inline:m", "text": "สวัสดีสมาชิก"},
        ],
    }
    payload.update(extra)
    return payload


def _package(out: Path, result: jobs.CampaignJobResult) -> dict[str, Any]:
    assert result.package is not None, result.error_code
    return dict(json.loads((out / result.package.filename).read_text(encoding="utf-8")))


def _codes(package: dict[str, Any]) -> list[str]:
    return [item["code"] for item in package.get("checks", [])]


# M1 copy rules on the structured build ------------------------------------------------------


def test_m1_platform_limit_rejects_and_engine_limit_flags(tmp_path: Path) -> None:
    long_ig = _payload()
    long_ig["assets"][0]["text"] = "ก" * 2_201
    assert run_campaign_job(long_ig, tmp_path / "a", "k").error_code == "copy_over_platform_limit"
    over_engine = _payload()
    over_engine["assets"][0]["text"] = "ก" * 501
    result = run_campaign_job(over_engine, tmp_path / "b", "k")
    assert result.status == "accepted"
    assert "copy_over_engine_limit" in _codes(_package(tmp_path / "b", result))


def test_m1_forbidden_words_reject_with_brand_list_and_word_boundaries(tmp_path: Path) -> None:
    baseline = _payload()
    baseline["assets"][0]["text"] = "Risk-free trial"
    assert run_campaign_job(baseline, tmp_path / "a", "k").error_code == "forbidden_word"
    brand = _payload(brand_rules={"forbidden_words": ["ฟรีตลอดชีพ"]})
    brand["assets"][1]["text"] = "สมัครวันนี้ ฟรีตลอดชีพ"
    assert run_campaign_job(brand, tmp_path / "b", "k").error_code == "forbidden_word"
    assert forbidden_hits("A secure checkout") == []
    assert forbidden_hits("ยาหายขาดใน 3 วัน") == ["หายขาด"]


def test_m1_claims_flag_and_markup_rejects(tmp_path: Path) -> None:
    claim = _payload()
    claim["assets"][0]["text"] = "ดีที่สุดในเมือง"
    result = run_campaign_job(claim, tmp_path / "a", "k")
    assert "claim_needs_evidence" in _codes(_package(tmp_path / "a", result))
    markup = _payload()
    markup["assets"][0]["text"] = "hi <script>x</script>"
    assert run_campaign_job(markup, tmp_path / "b", "k").error_code == "unsafe_copy"


def test_m1_plain_request_keeps_package_keys(tmp_path: Path) -> None:
    package = _package(tmp_path, run_campaign_job(_payload(), tmp_path, "k"))
    assert set(package) == {"schema_version", "campaign", "lineage", "telemetry"}


# M2 email subject / preheader ---------------------------------------------------------------


def test_m2_email_fields(tmp_path: Path) -> None:
    ok = _payload()
    ok["assets"][1].update(subject="ข่าวดีสำหรับสมาชิก " + "x" * 50, preheader="อ่านต่อด้านใน")
    package = _package(tmp_path / "a", run_campaign_job(ok, tmp_path / "a", "k"))
    mail = next(asset for asset in package["campaign"]["assets"] if asset["asset_id"] == "mail")
    assert mail["preheader"] == "อ่านต่อด้านใน" and "subject_above_recommended" in _codes(package)
    too_long = _payload()
    too_long["assets"][1]["subject"] = "s" * 151
    assert run_campaign_job(too_long, tmp_path / "b", "k").error_code == "subject_too_long"
    wrong_kind = _payload()
    wrong_kind["assets"][0]["subject"] = "hello"
    assert run_campaign_job(wrong_kind, tmp_path / "c", "k").error_code == "email_field_not_email"


# M3 planned dates ---------------------------------------------------------------------------


def test_m3_calendar_is_sorted_metadata(tmp_path: Path) -> None:
    payload = _payload(timezone="Asia/Bangkok")
    payload["assets"][0]["planned_at"] = "2026-12-03T09:30"
    payload["assets"][1]["planned_at"] = "2026-12-01"
    package = _package(tmp_path, run_campaign_job(payload, tmp_path, "k"))
    assert package["calendar"] == [
        {"planned_at": "2026-12-01", "channel": "email", "asset_id": "mail"},
        {"planned_at": "2026-12-03T09:30", "channel": "instagram", "asset_id": "ig-a"},
    ]
    assert package["campaign"]["timezone"] == "Asia/Bangkok"
    bad_date = _payload()
    bad_date["assets"][0]["planned_at"] = "2026-02-30"
    assert run_campaign_job(bad_date, tmp_path / "b", "k").error_code == "invalid_planned_at"
    assert run_campaign_job(_payload(timezone="Mars/Base"), tmp_path / "c", "k").error_code == "unknown_timezone"


# M4 bulk CSV --------------------------------------------------------------------------------


def test_m4_bulk_csv_rows_and_formula_escape(tmp_path: Path) -> None:
    payload = _payload(utm={"landing_url": "https://example.com/p"})
    payload["assets"][1]["text"] = "=Hello members"
    payload["assets"][1]["subject"] = "ข่าวดี"
    result = run_campaign_job(payload, tmp_path, "k")
    manifest = json.loads((tmp_path / str(result.manifest_filename)).read_text(encoding="utf-8"))
    export = manifest["exports"][0]
    raw = (tmp_path / export["filename"]).read_bytes()
    assert export["media_type"] == "text/csv" and raw.endswith(b"\r\n")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    assert list(rows[0]) == list(BULK_COLUMNS)
    assert [(row["channel"], row["asset_id"]) for row in rows] == [("email", "mail"), ("instagram", "ig-a")]
    assert rows[0]["text"] == "'=Hello members" and rows[0]["subject"] == "ข่าวดี"
    assert rows[1]["chars"] == "12" and rows[1]["char_limit"] == "500"
    assert rows[1]["tracked_link"].endswith("utm_content=ig-a")


def test_m4_image_row_has_frame_size(tmp_path: Path) -> None:
    (tmp_path / "hero.png").write_bytes(b"png")
    payload = _payload(asset_root=str(tmp_path))
    payload["assets"].append({"asset_id": "ig-img", "kind": "image", "channel": "instagram", "uri": "hero.png"})
    result = run_campaign_job(payload, tmp_path / "out", "k")
    manifest = json.loads((tmp_path / "out" / str(result.manifest_filename)).read_text(encoding="utf-8"))
    rows = list(csv.DictReader(io.StringIO((tmp_path / "out" / manifest["exports"][0]["filename"]).read_text(encoding="utf-8"))))
    image = next(row for row in rows if row["asset_id"] == "ig-img")
    assert image["frame_size"] == "1080x1350" and image["source"] == "hero.png" and image["text"] == ""


# M5 approval gate ---------------------------------------------------------------------------


def test_m5_approval_record_is_bound_to_package_hash(tmp_path: Path) -> None:
    result = run_campaign_job(_payload(), tmp_path, "k")
    assert result.package is not None
    package = tmp_path / result.package.filename
    assert approval_status(package)["status"] == "pending_review"
    record = record_approval(package, decision="approved", reviewer="owner", note="ok", decided_at="2026-11-12T10:00:00Z")
    assert record["package"]["sha256"] == result.package.content_hash
    assert approval_status(package)["status"] == "approved"
    package.write_text(package.read_text(encoding="utf-8").replace("Parity", "Changed"), encoding="utf-8")
    assert approval_status(package)["status"] == "stale"
    with pytest.raises(ValueError, match="invalid_decision"):
        record_approval(package, decision="publish", reviewer="owner")


def test_m5_cli_approve_and_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = run_campaign_job(_payload(), tmp_path, "k")
    assert result.package is not None
    package = str(tmp_path / result.package.filename)
    assert marketing_cli(["approval-status", "--package", package]) == 3
    assert marketing_cli(["approve", "--package", package, "--decision", "changes_requested", "--reviewer", "owner", "--note", "fix CTA"]) == 0
    assert marketing_cli(["approval-status", "--package", package]) == 3
    assert '"changes_requested"' in capsys.readouterr().out


# M6 A/B experiment plan ---------------------------------------------------------------------


def test_m6_sample_size_matches_reference() -> None:
    assert sample_size_per_variant(0.10, 0.20, variants=2) == 3841


def test_m6_experiment_plan_in_package(tmp_path: Path) -> None:
    payload = _payload(experiment_plan={"metric": "click_rate", "baseline_rate": 0.1, "minimum_detectable_lift": 0.2, "daily_reach_per_variant": 500})
    payload["assets"][0]["variant"] = "A"
    payload["assets"].append({"asset_id": "ig-b", "kind": "copy", "channel": "instagram", "uri": "inline:b", "text": "แบบ B", "variant": "B"})
    package = _package(tmp_path, run_campaign_job(payload, tmp_path, "k"))
    plan = package["experiments"][0]["plan"]
    assert plan == {"metric": "click_rate", "baseline_rate": 0.1, "minimum_detectable_lift": 0.2, "alpha": 0.05, "power": 0.8,
                    "sample_per_variant": 3841, "duration_days": 8}
    bad = _payload(experiment_plan={"metric": "x", "baseline_rate": 1.5, "minimum_detectable_lift": 0.2, "daily_reach_per_variant": 5})
    assert run_campaign_job(bad, tmp_path / "b", "k").status == "rejected"
