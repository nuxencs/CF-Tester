import importlib.util
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "report_results.py"
SPEC = importlib.util.spec_from_file_location("report_results", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load {SCRIPT}")
REPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORTER)


def result(name, passed, missing=None, unexpected=None):
    return {
        "Name": name,
        "Source": None,
        "inputType": "remoteRelease",
        "passed": passed,
        "matched": [],
        "missing": missing or [],
        "unexpected": unexpected or [],
        "diagnostics": [],
    }


class ReportResultsTests(unittest.TestCase):
    def test_writes_targeted_junit_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            radarr = root / "radarr.json"
            output = root / "results.xml"
            target = root / "target.json"
            radarr.write_text(
                json.dumps(
                    result("Radarr fail", False, missing=["expected-id"])
                ),
                encoding="utf-8",
            )
            target.write_text(
                json.dumps(
                    {
                        "application": "radarr",
                        "channel": "develop",
                        "upstreamChannel": "develop",
                        "version": "v6",
                        "commit": "a" * 40,
                    }
                ),
                encoding="utf-8",
            )

            summary = REPORTER.write_report(
                radarr, output, target
            )

            suite = ET.parse(output).getroot()
            self.assertEqual(suite.attrib["name"], "CF-Tester radarr/develop")
            self.assertEqual(suite.attrib["tests"], "1")
            self.assertEqual(suite.attrib["failures"], "1")
            self.assertEqual(summary.total, 1)
            self.assertEqual(summary.failed, 1)
            properties = {
                item.attrib["name"]: item.attrib["value"]
                for item in suite.findall("./properties/property")
            }
            self.assertEqual(properties["application"], "radarr")
            self.assertEqual(properties["channel"], "develop")
            self.assertEqual(properties["upstream.version"], "v6")
            self.assertEqual(properties["upstream.commit"], "a" * 40)
            case = suite.find("./testcase[@name='Radarr fail']")
            if case is None:
                self.fail("JUnit report has no Radarr fail test case")
            self.assertEqual(case.attrib["classname"], "radarr.develop.remoteRelease")
            failure = case.find("failure")
            if failure is None or failure.text is None:
                self.fail("JUnit report has no failure text")
            self.assertIn("Expected match IDs: expected-id", failure.text)

    def test_failure_text_uses_names_and_plain_specification_states(self):
        failed = result("Freeleech must match", False, missing=["trash-id"])
        failed["diagnostics"] = [
            {
                "trashId": "trash-id",
                "name": "FreeLeech",
                "expected": "match",
                "specifications": [
                    {
                        "name": "FreeLeech",
                        "implementation": "IndexerFlagSpecification",
                        "required": False,
                        "negate": False,
                        "matched": False,
                    }
                ],
            }
        ]

        text = REPORTER.failure_text(failed)

        self.assertEqual(
            text,
            "Expected match: FreeLeech (trash-id)\n"
            "  FreeLeech [Indexer flag]: not satisfied "
            "(optional, not negated)",
        )
        self.assertNotIn("Missing:", text)
        self.assertNotIn("matched=False", text)

    def test_reads_concatenated_json_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "results.json"
            path.write_text(
                json.dumps(result("One", True))
                + "\n"
                + json.dumps(result("Two", True)),
                encoding="utf-8",
            )

            values = REPORTER.read_results(path)

            self.assertEqual([value["Name"] for value in values], ["One", "Two"])

    def test_failure_text_explains_profile_score_differences(self):
        failed = result("SQP-4 score", False)
        failed["profileScore"] = {
            "expected": 2351,
            "actual": 2200,
            "passed": False,
            "formats": [],
            "differences": [
                {
                    "trashId": "format-id",
                    "name": "2160p",
                    "expected": 151,
                    "actual": 0,
                }
            ],
        }

        text = REPORTER.failure_text(failed)

        self.assertEqual(
            text,
            "Expected profile score 2351, got 2200\n"
            "  2160p (format-id): expected 151, got 0",
        )


if __name__ == "__main__":
    unittest.main()
