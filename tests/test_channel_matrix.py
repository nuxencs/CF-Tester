import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "channel_matrix.py"
SPEC = importlib.util.spec_from_file_location("channel_matrix", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load {SCRIPT}")
CHANNEL_MATRIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHANNEL_MATRIX)


def lock_data():
    return {
        "schemaVersion": 1,
        "applications": {
            "radarr": {
                "stable": {
                    "upstreamChannel": "master",
                    "version": "6.3.0.10514",
                    "commit": "a" * 40,
                },
                "develop": {
                    "upstreamChannel": "develop",
                    "version": "6.4.1.10545",
                    "commit": "b" * 40,
                },
                "nightly": {
                    "upstreamChannel": "nightly",
                    "version": "6.4.2.10574",
                    "commit": "c" * 40,
                },
            },
            "sonarr": {
                "stable": {
                    "upstreamChannel": "main",
                    "version": "4.0.19.2979",
                    "commit": "d" * 40,
                },
                "develop": {
                    "upstreamChannel": "develop",
                    "version": "4.0.19.3001",
                    "commit": "e" * 40,
                },
            },
        },
    }


class ChannelLockTests(unittest.TestCase):
    def test_loads_complete_channel_matrix(self):
        matrix = CHANNEL_MATRIX.ChannelMatrix(lock_data())

        self.assertEqual(
            [target.key for target in matrix.targets],
            [
                "radarr/stable",
                "radarr/develop",
                "radarr/nightly",
                "sonarr/stable",
                "sonarr/develop",
            ],
        )
        self.assertEqual(matrix.target("sonarr", "develop").commit, "e" * 40)

    def test_rejects_missing_required_channel(self):
        data = lock_data()
        del data["applications"]["radarr"]["nightly"]

        with self.assertRaisesRegex(
            CHANNEL_MATRIX.MatrixError, "missing channel: radarr/nightly"
        ):
            CHANNEL_MATRIX.ChannelMatrix(data)

    def test_rejects_unknown_channel(self):
        data = lock_data()
        data["applications"]["sonarr"]["nightly"] = data["applications"][
            "sonarr"
        ]["develop"]

        with self.assertRaisesRegex(
            CHANNEL_MATRIX.MatrixError, "unsupported channel: sonarr/nightly"
        ):
            CHANNEL_MATRIX.ChannelMatrix(data)

    def test_rejects_nonimmutable_commit(self):
        data = lock_data()
        data["applications"]["radarr"]["stable"]["commit"] = "master"

        with self.assertRaisesRegex(
            CHANNEL_MATRIX.MatrixError,
            "radarr/stable commit must be a full lowercase Git SHA",
        ):
            CHANNEL_MATRIX.ChannelMatrix(data)


class ReleaseManifestTests(unittest.TestCase):
    def test_builds_and_reads_verified_release_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            matrix = CHANNEL_MATRIX.ChannelMatrix(lock_data())
            for target in matrix.targets:
                binary = artifacts / target.binary_archive
                source = artifacts / target.source_archive
                binary.write_bytes(target.key.encode())
                source.write_bytes(f"source:{target.key}".encode())

            manifest = CHANNEL_MATRIX.build_release_manifest(
                matrix, "v0.2.0", artifacts
            )
            selected = CHANNEL_MATRIX.ReleaseManifest(manifest).target(
                "radarr", "nightly"
            )

            expected = hashlib.sha256(b"radarr/nightly").hexdigest()
            self.assertEqual(selected.sha256, expected)
            self.assertEqual(selected.commit, "c" * 40)
            self.assertEqual(
                selected.artifact,
                "cf-tester-radarr-nightly-linux-x64.tar.gz",
            )

    def test_manifest_rejects_target_metadata_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            matrix = CHANNEL_MATRIX.ChannelMatrix(lock_data())
            for target in matrix.targets:
                (artifacts / target.binary_archive).write_bytes(b"binary")
                (artifacts / target.source_archive).write_bytes(b"source")
            manifest = CHANNEL_MATRIX.build_release_manifest(
                matrix, "v0.2.0", artifacts
            )
            manifest["targets"]["radarr/stable"]["application"] = "sonarr"

            with self.assertRaisesRegex(
                CHANNEL_MATRIX.MatrixError,
                "radarr/stable manifest metadata does not match its target",
            ):
                CHANNEL_MATRIX.ReleaseManifest(manifest)

    def test_artifact_metadata_must_match_manifest(self):
        target = CHANNEL_MATRIX.ChannelMatrix(lock_data()).target(
            "sonarr", "stable"
        )
        manifest_target = {
            **target.as_dict(),
            "artifact": target.binary_archive,
            "sha256": "1" * 64,
            "sourceArtifact": target.source_archive,
            "sourceSha256": "2" * 64,
        }
        artifact_target = target.as_dict()
        artifact_target["version"] = "wrong"

        with self.assertRaisesRegex(
            CHANNEL_MATRIX.MatrixError,
            "artifact metadata does not match release manifest",
        ):
            CHANNEL_MATRIX.verify_artifact_metadata(
                manifest_target, artifact_target
            )


if __name__ == "__main__":
    unittest.main()
