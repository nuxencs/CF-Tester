#!/usr/bin/env python3
"""Combine worker JSON output into concise console and JUnit results."""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import NamedTuple


class Summary(NamedTuple):
    total: int
    failed: int
    counts: Counter


def read_results(path: Path) -> list[dict]:
    """Read the sequence of JSON values emitted by one worker."""
    source = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    values = []
    offset = 0
    while offset < len(source):
        while offset < len(source) and source[offset].isspace():
            offset += 1
        if offset == len(source):
            break
        value, offset = decoder.raw_decode(source, offset)
        values.append(value)
    return values


def failure_text(result: dict) -> str:
    lines = []
    diagnostics = result.get("diagnostics", [])
    for diagnostic in diagnostics:
        expected = "match" if diagnostic["expected"] == "match" else "no match"
        lines.append(f"Expected {expected}: {diagnostic['name']} ({diagnostic['trashId']})")
        for specification in diagnostic.get("specifications", []):
            implementation = specification["implementation"].removesuffix(
                "Specification"
            )
            implementation = re.sub(r"(?<!^)(?=[A-Z])", " ", implementation).lower()
            state = "satisfied" if specification["matched"] else "not satisfied"
            requirement = "required" if specification["required"] else "optional"
            negation = "negated" if specification["negate"] else "not negated"
            lines.append(
                f"  {specification['name']} [{implementation.capitalize()}]: "
                f"{state} ({requirement}, {negation})"
            )
    if not diagnostics and result.get("missing"):
        lines.append(f"Expected match IDs: {', '.join(result['missing'])}")
    if not diagnostics and result.get("unexpected"):
        lines.append(f"Expected no-match IDs: {', '.join(result['unexpected'])}")
    profile_score = result.get("profileScore")
    if profile_score and not profile_score.get("passed", False):
        lines.append(
            f"Expected profile score {profile_score['expected']}, "
            f"got {profile_score['actual']}"
        )
        for difference in profile_score.get("differences", []):
            expected = difference.get("expected")
            actual = difference.get("actual")
            if expected is None:
                detail = f"expected no score, got {actual}"
            elif actual is None:
                detail = f"expected {expected}, got no score"
            else:
                detail = f"expected {expected}, got {actual}"
            lines.append(
                f"  {difference['name']} ({difference['trashId']}): {detail}"
            )
    return "\n".join(lines) or "Custom Format assertion failed."


def read_target(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_report(
    result_path: Path, output: Path, target_path: Path
) -> Summary:
    target = read_target(target_path)
    application = target["application"]
    channel = target["channel"]
    suite = ET.Element("testsuite", name=f"CF-Tester {application}/{channel}")
    properties = ET.SubElement(suite, "properties")
    property_values = {
        "application": application,
        "channel": channel,
        "upstream.channel": target["upstreamChannel"],
        "upstream.version": target["version"],
        "upstream.commit": target["commit"],
    }
    for name, value in property_values.items():
        ET.SubElement(properties, "property", name=name, value=value)

    results = read_results(result_path)
    counts = Counter("passed" if result["passed"] else "failed" for result in results)
    total = 0
    failed = 0
    for result in results:
        total += 1
        case = ET.SubElement(
            suite,
            "testcase",
            name=result["Name"],
            classname=f"{application}.{channel}.{result['inputType']}",
        )
        if not result["passed"]:
            failed += 1
            text = failure_text(result)
            failure = ET.SubElement(case, "failure", message=text.splitlines()[0])
            failure.text = text
        details = ET.SubElement(case, "system-out")
        details.text = json.dumps(result, indent=2)

    suite.set("tests", str(total))
    suite.set("failures", str(failed))
    suite.set("errors", "0")
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(output, encoding="utf-8", xml_declaration=True)
    return Summary(total=total, failed=failed, counts=counts)


def escape_workflow_command(value: str) -> str:
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def print_summary(
    summary: Summary,
    result_path: Path,
    junit_path: Path,
    target_path: Path,
) -> None:
    target = read_target(target_path)
    application = target["application"]
    channel = target["channel"]
    label = f"{application.capitalize()} {channel}"
    result_line = (
        f"{label}: {summary.counts['passed']} passed, "
        f"{summary.counts['failed']} failed"
    )
    upstream_line = f"Upstream: {target['version']} ({target['commit']})"
    print(result_line)
    print(upstream_line)
    lines = [
        f"## Custom Format tests: {label}",
        "",
        f"- {result_line}",
        f"- {upstream_line}",
        "",
        f"JUnit: `{junit_path}`",
    ]

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as file:
            file.write("\n".join(lines) + "\n")

    if os.environ.get("GITHUB_ACTIONS") == "true":
        for result in read_results(result_path):
            if result["passed"]:
                continue
            title = escape_workflow_command(f"{label}: {result['Name']}")
            message = escape_workflow_command(failure_text(result))
            print(f"::error title={title}::{message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    ARGS = parse_args()
    report = write_report(ARGS.results, ARGS.junit, ARGS.target)
    print_summary(report, ARGS.results, ARGS.junit, ARGS.target)
    sys.exit(1 if report.failed else 0)
