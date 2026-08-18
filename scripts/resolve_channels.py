#!/usr/bin/env python3
"""Resolve official Radarr and Sonarr channels and report pin drift."""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from channel_matrix import ChannelMatrix, MatrixError


RADARR_FEED = (
    "https://radarr.servarr.com/v1/update/{channel}/changes"
    "?runtime=netcore&os=linuxmusl"
)
SONARR_RELEASES = "https://services.sonarr.tv/v1/releases"
GITHUB_COMMIT = "https://api.github.com/repos/{repository}/commits/v{version}"
AZURE_BUILD = (
    "https://dev.azure.com/Radarr/Radarr/_apis/build/builds/{build_id}"
    "?api-version=7.1"
)
AZURE_BUILD_PATTERN = re.compile(r"/_apis/build/builds/(\d+)")


class ResolveError(RuntimeError):
    """An official channel could not be resolved to an immutable commit."""


def fetch_json(url: str):
    headers = {"Accept": "application/json", "User-Agent": "CF-Tester"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise ResolveError(f"cannot read {url}: {error}") from error


def github_version_commit(
    fetch: Callable[[str], object], repository: str, version: str
) -> str:
    response = fetch(GITHUB_COMMIT.format(repository=repository, version=version))
    if not isinstance(response, dict) or not isinstance(response.get("sha"), str):
        raise ResolveError(f"GitHub did not resolve {repository} v{version}")
    return response["sha"]


def resolve_radarr(fetch: Callable[[str], object]) -> dict:
    resolved = {}
    for channel, upstream_channel in (
        ("stable", "master"),
        ("develop", "develop"),
        ("nightly", "nightly"),
    ):
        response = fetch(RADARR_FEED.format(channel=upstream_channel))
        if not isinstance(response, list) or not response:
            raise ResolveError(f"Radarr {upstream_channel} feed is empty")
        release = response[0]
        if not isinstance(release, dict) or not isinstance(
            release.get("version"), str
        ):
            raise ResolveError(f"Radarr {upstream_channel} feed has no version")
        version = release["version"]
        if channel == "nightly":
            build_url = release.get("url", "")
            match = AZURE_BUILD_PATTERN.search(build_url)
            if match is None:
                raise ResolveError(
                    "Radarr nightly feed does not identify an Azure build"
                )
            build = fetch(AZURE_BUILD.format(build_id=match.group(1)))
            if not isinstance(build, dict) or not isinstance(
                build.get("sourceVersion"), str
            ):
                raise ResolveError("Radarr nightly Azure build has no sourceVersion")
            commit = build["sourceVersion"]
        else:
            commit = github_version_commit(fetch, "Radarr/Radarr", version)
        resolved[channel] = {
            "upstreamChannel": upstream_channel,
            "version": version,
            "commit": commit,
        }
    return resolved


def resolve_sonarr(fetch: Callable[[str], object]) -> dict:
    response = fetch(SONARR_RELEASES)
    if isinstance(response, dict):
        releases = list(response.values())
    elif isinstance(response, list):
        releases = response
    else:
        raise ResolveError("Sonarr release service returned an unsupported value")
    resolved = {}
    for channel, release_channel, upstream_channel in (
        ("stable", "v4-stable", "main"),
        ("develop", "v4-nightly", "develop"),
    ):
        release = next(
            (
                item
                for item in releases
                if isinstance(item, dict)
                and item.get("releaseChannel") == release_channel
            ),
            None,
        )
        if release is None or not isinstance(release.get("version"), str):
            raise ResolveError(f"Sonarr release service has no {release_channel}")
        version = release["version"]
        resolved[channel] = {
            "upstreamChannel": upstream_channel,
            "version": version,
            "commit": github_version_commit(fetch, "Sonarr/Sonarr", version),
        }
    return resolved


def resolve_channels(fetch: Callable[[str], object] = fetch_json) -> ChannelMatrix:
    return ChannelMatrix(
        {
            "schemaVersion": 1,
            "applications": {
                "radarr": resolve_radarr(fetch),
                "sonarr": resolve_sonarr(fetch),
            },
        }
    )


def build_watch_matrix(
    current: ChannelMatrix, locked: ChannelMatrix | None = None
) -> dict[str, list[dict[str, str | bool]]]:
    """Build a GitHub matrix with optional pin comparison fields."""
    targets = []
    for target in current.targets:
        values: dict[str, str | bool] = target.as_dict()
        if locked is not None:
            pinned = locked.target(target.application, target.channel)
            values.update(
                {
                    "pinnedVersion": pinned.version,
                    "pinnedCommit": pinned.commit,
                    "drift": target != pinned,
                }
            )
        targets.append(values)
    return {"include": targets}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lock",
        type=Path,
        help="compare resolved targets with this channel lock",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        matrix = resolve_channels()
        locked = ChannelMatrix.from_path(args.lock) if args.lock else None
    except (ResolveError, MatrixError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps(build_watch_matrix(matrix, locked), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
