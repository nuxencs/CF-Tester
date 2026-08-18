# CF-Tester

CF-Tester checks TRaSH custom formats with the Radarr and Sonarr code that the
formats target. It runs release-title and file-name cases without starting the
applications or using a database. It also checks Custom Format scores from
Guides quality profiles.

## Local tests

Install these tools:

- Git, Python 3, and `jq`
- .NET 8 SDK for Radarr
- .NET 6 SDK for Sonarr
- Local checkouts of Guides and the application source

The application checkout must be at the commit in `channels.lock.json` for the
selected channel. Run one application and channel at a time:

```bash
GUIDES_DIR=/path/to/Guides \
RADARR_SOURCE=/path/to/Radarr \
./scripts/test-from-source.sh radarr stable

GUIDES_DIR=/path/to/Guides \
SONARR_SOURCE=/path/to/Sonarr \
./scripts/test-from-source.sh sonarr stable
```

Set `RADARR_DOTNET` or `SONARR_DOTNET` if the required SDK is not available as
`dotnet`.

Run the repository checks with:

```bash
./scripts/check-repository.sh /path/to/Guides
```

## Supported channels

`channels.lock.json` pins these targets to exact versions and commits:

- Radarr stable, develop, and nightly
- Sonarr stable and develop

The weekly watcher resolves the official application feeds, compares them with
the pins, and tests the resolved source. It reports drift but does not update
the lock file.

## GitHub Action

One action run tests one application and channel. The action verifies the
release manifest and selected Linux worker, reports failures as annotations and
a job summary, and writes a JUnit report. It downloads workers from the same
repository as the action.

The validation workflow belongs in the Guides repository. See
[`docs/guides-integration.md`](docs/guides-integration.md) for the integration
steps and required fixed versions.

## Releases

The release workflow builds a self-contained Linux x64 worker and source
archive for each supported target. It publishes their checksums in one release
manifest.

## Scope

See [`docs/scope.md`](docs/scope.md) for the tested code paths, supported
case data, and limits.
