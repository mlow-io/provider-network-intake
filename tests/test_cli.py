from __future__ import annotations

import json
import subprocess
import sys

from provider_network_intake.demo import run_demo


def test_synthetic_demo_restarts_recognizes_and_exports(tmp_path):
    root = tmp_path / "demo"
    result = run_demo(root)
    assert result["before_review"] == "unknown"
    assert result["after_restart"] == "exact"
    assert result["other_source"] == "unknown"
    assert result["model_invocations"] == 0
    assert result["north_directory_visible"] is False
    assert result["south_directory_visible"] is True
    assert result["participation_export"] == "held"
    assert result["import_authority"] == "proposal_only"
    original = (root / "delivery-b.csv").read_bytes()
    again = subprocess.run(
        [sys.executable, "-m", "provider_network_intake.cli", "demo", "--root", str(root)],
        capture_output=True,
        text=True,
    )
    assert again.returncode == 1
    assert (root / "delivery-b.csv").read_bytes() == original


def test_cli_preview_held_exit_code_and_exclusive_output(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("Practitioner NPI,Office ID\n1000000004,NORTH\n")
    output = tmp_path / "preview.json"
    command = [
        sys.executable,
        "-m",
        "provider_network_intake.cli",
        "preview",
        str(source),
        "--source",
        "synthetic",
        "--delivery",
        "one",
        "--output",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(output.read_text())["eligibility"]["state"] == "held"
    before = output.read_bytes()
    repeated = subprocess.run(command, capture_output=True, text=True)
    assert repeated.returncode == 1
    assert output.read_bytes() == before


def test_cli_native_reference_preview(tmp_path):
    source = tmp_path / "registry.json"
    source.write_text(json.dumps({"result_count": 0, "results": []}))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "provider_network_intake.cli",
            "preview",
            str(source),
            "--source",
            "synthetic",
            "--delivery",
            "one",
            "--format",
            "nppes",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["layers"]["profile"] == "not_applicable"


def test_cli_inspect_malformed_csv_returns_structured_failure(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("NPI,NPI\na,b\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "provider_network_intake.cli",
            "inspect",
            str(source),
            "--source",
            "synthetic",
            "--delivery",
            "one",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert json.loads(result.stderr)["error"] == "IntakeError"


def test_cli_report_emits_markdown_and_json(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("Practitioner NPI,Office ID\n1234567890,NORTH\n")
    out_md = tmp_path / "report.md"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "provider_network_intake.cli",
            "report",
            str(source),
            "--source",
            "synthetic",
            "--delivery",
            "one",
            "--output",
            str(out_md),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    rendered = out_md.read_text(encoding="utf-8")
    assert rendered.startswith("# Provider Network Intake Report\n")
    assert "`invalid_npi_checksum`" in rendered

    json_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "provider_network_intake.cli",
            "report",
            str(source),
            "--source",
            "synthetic",
            "--delivery",
            "one",
            "--report-format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    assert json_result.returncode == 2
    assert json.loads(json_result.stdout)["findings"]["counts"]["invalid_npi_checksum"] == 1
