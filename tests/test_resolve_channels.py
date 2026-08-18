import copy
import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "resolve_channels.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("resolve_channels", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load {SCRIPT}")
RESOLVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESOLVER)


class ResolveChannelsTests(unittest.TestCase):
    def test_resolves_official_feeds_to_exact_commits(self):
        responses = {
            "radarr/master": [
                {"version": "6.3.0.1", "url": "https://example.invalid/stable"}
            ],
            "radarr/develop": [
                {"version": "6.4.0.2", "url": "https://example.invalid/develop"}
            ],
            "radarr/nightly": [
                {
                    "version": "6.5.0.3",
                    "url": (
                        "https://dev.azure.com/Radarr/Radarr/"
                        "_apis/build/builds/9220/artifacts"
                    ),
                }
            ],
            "github/Radarr/v6.3.0.1": {"sha": "a" * 40},
            "github/Radarr/v6.4.0.2": {"sha": "b" * 40},
            "azure/9220": {"sourceVersion": "c" * 40},
            "sonarr/releases": {
                "v4-stable": {
                    "releaseChannel": "v4-stable",
                    "version": "4.0.1.1",
                    "branch": "main",
                },
                "v4-nightly": {
                    "releaseChannel": "v4-nightly",
                    "version": "4.0.2.2",
                    "branch": "develop",
                },
            },
            "github/Sonarr/v4.0.1.1": {"sha": "d" * 40},
            "github/Sonarr/v4.0.2.2": {"sha": "e" * 40},
        }

        def fetch(url):
            if "radarr.servarr.com/v1/update/" in url:
                channel = url.split("/update/", 1)[1].split("/", 1)[0]
                return responses[f"radarr/{channel}"]
            if "api.github.com/repos/Radarr" in url:
                version = url.rsplit("/", 1)[1]
                return responses[f"github/Radarr/{version}"]
            if "_apis/build/builds/9220" in url:
                return responses["azure/9220"]
            if "services.sonarr.tv/v1/releases" in url:
                return responses["sonarr/releases"]
            if "api.github.com/repos/Sonarr" in url:
                version = url.rsplit("/", 1)[1]
                return responses[f"github/Sonarr/{version}"]
            raise AssertionError(f"unexpected URL: {url}")

        matrix = RESOLVER.resolve_channels(fetch)

        self.assertEqual(matrix.target("radarr", "stable").commit, "a" * 40)
        self.assertEqual(matrix.target("radarr", "nightly").commit, "c" * 40)
        self.assertEqual(matrix.target("sonarr", "develop").version, "4.0.2.2")

    def test_rejects_nightly_without_official_build_identity(self):
        def fetch(url):
            if "/update/master/" in url:
                return [{"version": "6.3.0.1", "url": "stable"}]
            if "/update/develop/" in url:
                return [{"version": "6.4.0.2", "url": "develop"}]
            if "/update/nightly/" in url:
                return [{"version": "6.5.0.3", "url": "missing-build-id"}]
            if "api.github.com/repos/Radarr" in url:
                return {"sha": "a" * 40}
            raise AssertionError(f"unexpected URL: {url}")

        with self.assertRaisesRegex(
            RESOLVER.ResolveError,
            "Radarr nightly feed does not identify an Azure build",
        ):
            RESOLVER.resolve_channels(fetch)

    def test_compares_resolved_targets_with_pins(self):
        locked_data = {
            "schemaVersion": 1,
            "applications": {
                "radarr": {
                    "stable": {
                        "upstreamChannel": "master",
                        "version": "1.0.0",
                        "commit": "a" * 40,
                    },
                    "develop": {
                        "upstreamChannel": "develop",
                        "version": "2.0.0",
                        "commit": "b" * 40,
                    },
                    "nightly": {
                        "upstreamChannel": "nightly",
                        "version": "3.0.0",
                        "commit": "c" * 40,
                    },
                },
                "sonarr": {
                    "stable": {
                        "upstreamChannel": "main",
                        "version": "4.0.0",
                        "commit": "d" * 40,
                    },
                    "develop": {
                        "upstreamChannel": "develop",
                        "version": "5.0.0",
                        "commit": "e" * 40,
                    },
                },
            },
        }
        locked = RESOLVER.ChannelMatrix(locked_data)
        current_data = copy.deepcopy(locked_data)
        current_data["applications"]["sonarr"]["develop"]["version"] = "5.0.1"
        current_data["applications"]["sonarr"]["develop"]["commit"] = "f" * 40

        matrix = RESOLVER.build_watch_matrix(
            RESOLVER.ChannelMatrix(current_data), locked
        )

        by_key = {
            f"{target['application']}/{target['channel']}": target
            for target in matrix["include"]
        }
        self.assertFalse(by_key["radarr/stable"]["drift"])
        self.assertTrue(by_key["sonarr/develop"]["drift"])
        self.assertEqual(by_key["sonarr/develop"]["pinnedVersion"], "5.0.0")
        self.assertEqual(by_key["sonarr/develop"]["pinnedCommit"], "e" * 40)


if __name__ == "__main__":
    unittest.main()
