#!/usr/bin/env python3
"""Validate channel locks and select immutable worker targets."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple


SUPPORTED_CHANNELS = {
    "radarr": ("stable", "develop", "nightly"),
    "sonarr": ("stable", "develop"),
}
SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
RELEASE_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class MatrixError(ValueError):
    """A channel lock, target, or release manifest is invalid."""


class Target(NamedTuple):
    application: str
    channel: str
    upstream_channel: str
    version: str
    commit: str

    @property
    def key(self) -> str:
        return f"{self.application}/{self.channel}"

    @property
    def binary_archive(self) -> str:
        return f"cf-tester-{self.application}-{self.channel}-linux-x64.tar.gz"

    @property
    def source_archive(self) -> str:
        return f"cf-tester-{self.application}-{self.channel}-source.tar.gz"

    def as_dict(self) -> dict[str, str]:
        return {
            "application": self.application,
            "channel": self.channel,
            "upstreamChannel": self.upstream_channel,
            "version": self.version,
            "commit": self.commit,
        }


class ReleaseTarget(NamedTuple):
    application: str
    channel: str
    upstream_channel: str
    version: str
    commit: str
    artifact: str
    sha256: str
    source_artifact: str
    source_sha256: str

    @property
    def key(self) -> str:
        return f"{self.application}/{self.channel}"

    def as_dict(self) -> dict[str, str]:
        return {
            "application": self.application,
            "channel": self.channel,
            "upstreamChannel": self.upstream_channel,
            "version": self.version,
            "commit": self.commit,
            "artifact": self.artifact,
            "sha256": self.sha256,
            "sourceArtifact": self.source_artifact,
            "sourceSha256": self.source_sha256,
        }


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MatrixError(f"cannot read JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise MatrixError(f"{path} must contain a JSON object")
    return value


def validate_target(data: dict, expected_key: str | None = None) -> Target:
    required = {
        "application",
        "channel",
        "upstreamChannel",
        "version",
        "commit",
    }
    if set(data) != required:
        raise MatrixError("target metadata must contain only the required fields")

    application = data.get("application")
    channel = data.get("channel")
    if not isinstance(application, str) or not isinstance(channel, str):
        raise MatrixError("target application and channel must be strings")
    key = f"{application}/{channel}"
    if application not in SUPPORTED_CHANNELS or channel not in SUPPORTED_CHANNELS.get(
        application, ()
    ):
        raise MatrixError(f"unsupported channel: {key}")
    if expected_key is not None and key != expected_key:
        raise MatrixError(f"{expected_key} manifest metadata does not match its target")

    upstream_channel = data.get("upstreamChannel")
    version = data.get("version")
    commit = data.get("commit")
    if not isinstance(upstream_channel, str) or not upstream_channel:
        raise MatrixError(f"{key} upstreamChannel must be a non-empty string")
    if not isinstance(version, str) or not version:
        raise MatrixError(f"{key} version must be a non-empty string")
    if not isinstance(commit, str) or not SHA_PATTERN.fullmatch(commit):
        raise MatrixError(f"{key} commit must be a full lowercase Git SHA")

    return Target(application, channel, upstream_channel, version, commit)


class ChannelMatrix:
    """The complete, immutable set of supported worker targets."""

    def __init__(self, data: dict):
        if set(data) != {"schemaVersion", "applications"}:
            raise MatrixError("channel lock must contain schemaVersion and applications")
        if data.get("schemaVersion") != 1:
            raise MatrixError("unsupported channel lock schemaVersion")
        applications = data.get("applications")
        if not isinstance(applications, dict):
            raise MatrixError("applications must be an object")

        unknown_applications = set(applications) - set(SUPPORTED_CHANNELS)
        if unknown_applications:
            name = sorted(unknown_applications)[0]
            raise MatrixError(f"unsupported application: {name}")

        targets = []
        for application, supported_channels in SUPPORTED_CHANNELS.items():
            channel_data = applications.get(application)
            if not isinstance(channel_data, dict):
                raise MatrixError(f"missing application: {application}")
            unknown_channels = set(channel_data) - set(supported_channels)
            if unknown_channels:
                channel = sorted(unknown_channels)[0]
                raise MatrixError(f"unsupported channel: {application}/{channel}")
            for channel in supported_channels:
                values = channel_data.get(channel)
                if not isinstance(values, dict):
                    raise MatrixError(f"missing channel: {application}/{channel}")
                targets.append(
                    validate_target(
                        {
                            "application": application,
                            "channel": channel,
                            **values,
                        }
                    )
                )
        self.targets = tuple(targets)

    @classmethod
    def from_path(cls, path: Path) -> "ChannelMatrix":
        return cls(read_json(path))

    def target(self, application: str, channel: str) -> Target:
        key = f"{application}/{channel}"
        for target in self.targets:
            if target.key == key:
                return target
        raise MatrixError(f"unsupported channel: {key}")

    def github_matrix(self) -> dict[str, list[dict[str, str]]]:
        return {"include": [target.as_dict() for target in self.targets]}


def parse_release_target(key: str, data: dict) -> ReleaseTarget:
    metadata_keys = {
        "application",
        "channel",
        "upstreamChannel",
        "version",
        "commit",
    }
    required = metadata_keys | {
        "artifact",
        "sha256",
        "sourceArtifact",
        "sourceSha256",
    }
    if set(data) != required:
        raise MatrixError(f"{key} release target must contain only required fields")
    target = validate_target(
        {name: data[name] for name in metadata_keys}, expected_key=key
    )
    if data["artifact"] != target.binary_archive:
        raise MatrixError(f"{key} artifact name does not match its target")
    if data["sourceArtifact"] != target.source_archive:
        raise MatrixError(f"{key} source artifact name does not match its target")
    if not SHA256_PATTERN.fullmatch(data["sha256"]):
        raise MatrixError(f"{key} sha256 must be a lowercase SHA-256 value")
    if not SHA256_PATTERN.fullmatch(data["sourceSha256"]):
        raise MatrixError(f"{key} sourceSha256 must be a lowercase SHA-256 value")
    return ReleaseTarget(
        *target,
        data["artifact"],
        data["sha256"],
        data["sourceArtifact"],
        data["sourceSha256"],
    )


class ReleaseManifest:
    """A verified index of immutable worker release artifacts."""

    def __init__(self, data: dict):
        if set(data) != {"schemaVersion", "release", "targets"}:
            raise MatrixError(
                "release manifest must contain schemaVersion, release, and targets"
            )
        if data.get("schemaVersion") != 1:
            raise MatrixError("unsupported release manifest schemaVersion")
        release = data.get("release")
        if not isinstance(release, str) or not RELEASE_PATTERN.fullmatch(release):
            raise MatrixError("release must be a safe release tag")
        target_data = data.get("targets")
        if not isinstance(target_data, dict):
            raise MatrixError("release manifest targets must be an object")

        expected_keys = {
            f"{application}/{channel}"
            for application, channels in SUPPORTED_CHANNELS.items()
            for channel in channels
        }
        missing = expected_keys - set(target_data)
        unknown = set(target_data) - expected_keys
        if missing:
            raise MatrixError(f"release manifest missing target: {sorted(missing)[0]}")
        if unknown:
            raise MatrixError(f"release manifest has unknown target: {sorted(unknown)[0]}")

        self.release = release
        self.targets = tuple(
            parse_release_target(key, target_data[key])
            for key in sorted(target_data)
        )

    @classmethod
    def from_path(cls, path: Path) -> "ReleaseManifest":
        return cls(read_json(path))

    def target(self, application: str, channel: str) -> ReleaseTarget:
        key = f"{application}/{channel}"
        for target in self.targets:
            if target.key == key:
                return target
        raise MatrixError(f"unsupported channel: {key}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_release_manifest(
    matrix: ChannelMatrix, release: str, artifacts_dir: Path
) -> dict:
    if not RELEASE_PATTERN.fullmatch(release):
        raise MatrixError("release must be a safe release tag")
    targets = {}
    for target in matrix.targets:
        binary = artifacts_dir / target.binary_archive
        source = artifacts_dir / target.source_archive
        if not binary.is_file():
            raise MatrixError(f"missing release artifact: {binary}")
        if not source.is_file():
            raise MatrixError(f"missing source artifact: {source}")
        targets[target.key] = {
            **target.as_dict(),
            "artifact": target.binary_archive,
            "sha256": sha256_file(binary),
            "sourceArtifact": target.source_archive,
            "sourceSha256": sha256_file(source),
        }
    manifest = {"schemaVersion": 1, "release": release, "targets": targets}
    ReleaseManifest(manifest)
    return manifest


def verify_artifact_metadata(manifest_target: dict, artifact_target: dict) -> None:
    metadata_keys = (
        "application",
        "channel",
        "upstreamChannel",
        "version",
        "commit",
    )
    if any(
        manifest_target.get(key) != artifact_target.get(key)
        for key in metadata_keys
    ):
        raise MatrixError("artifact metadata does not match release manifest")
    validate_target(artifact_target)


def print_target(target: Target | ReleaseTarget, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(target.as_dict(), separators=(",", ":")))
        return
    if isinstance(target, ReleaseTarget):
        print(
            "\t".join(
                (
                    target.artifact,
                    target.sha256,
                    target.version,
                    target.commit,
                    target.upstream_channel,
                )
            )
        )
        return
    print(
        "\t".join(
            (
                target.application,
                target.channel,
                target.upstream_channel,
                target.version,
                target.commit,
            )
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    lock_matrix = subparsers.add_parser("lock-matrix")
    lock_matrix.add_argument("lock", type=Path)

    lock_target = subparsers.add_parser("lock-target")
    lock_target.add_argument("lock", type=Path)
    lock_target.add_argument("application")
    lock_target.add_argument("channel")
    lock_target.add_argument("--format", choices=("json", "tsv"), default="json")

    manifest_target = subparsers.add_parser("manifest-target")
    manifest_target.add_argument("manifest", type=Path)
    manifest_target.add_argument("application")
    manifest_target.add_argument("channel")
    manifest_target.add_argument("--release")
    manifest_target.add_argument(
        "--format", choices=("json", "tsv"), default="json"
    )

    validate_artifact = subparsers.add_parser("validate-artifact")
    validate_artifact.add_argument("target", type=Path)
    validate_artifact.add_argument("application")
    validate_artifact.add_argument("channel")
    validate_artifact.add_argument("--manifest", type=Path)
    validate_artifact.add_argument(
        "--format", choices=("json", "tsv"), default="json"
    )

    build_manifest = subparsers.add_parser("build-manifest")
    build_manifest.add_argument("lock", type=Path)
    build_manifest.add_argument("release")
    build_manifest.add_argument("artifacts", type=Path)
    build_manifest.add_argument("output", type=Path)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "lock-matrix":
            matrix = ChannelMatrix.from_path(args.lock)
            print(json.dumps(matrix.github_matrix(), separators=(",", ":")))
        elif args.command == "lock-target":
            target = ChannelMatrix.from_path(args.lock).target(
                args.application, args.channel
            )
            print_target(target, args.format)
        elif args.command == "manifest-target":
            manifest = ReleaseManifest.from_path(args.manifest)
            if args.release is not None and manifest.release != args.release:
                raise MatrixError(
                    "release manifest does not match the requested release"
                )
            target = manifest.target(args.application, args.channel)
            print_target(target, args.format)
        elif args.command == "validate-artifact":
            artifact_target = read_json(args.target)
            expected_key = f"{args.application}/{args.channel}"
            target = validate_target(artifact_target, expected_key)
            if args.manifest is not None:
                release_target = ReleaseManifest.from_path(args.manifest).target(
                    args.application, args.channel
                )
                verify_artifact_metadata(release_target.as_dict(), artifact_target)
            print_target(target, args.format)
        elif args.command == "build-manifest":
            manifest = build_release_manifest(
                ChannelMatrix.from_path(args.lock), args.release, args.artifacts
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
    except MatrixError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
