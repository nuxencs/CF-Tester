# What CF-Tester Tests

CF-Tester tests Custom Format matching and quality profile scores. It does not
test whether Radarr or Sonarr will grab, reject, import, upgrade, or rename a
release.

## Exact code paths

Each worker runs code from its pinned upstream commit:

1. The Radarr or Sonarr title parser.
2. The upstream remote-release language augmenter.
3. The upstream `CustomFormatCalculationService`.
4. The upstream local-file or stored-file entry point, when the case selects
   that input type.
5. The upstream `QualityProfile.CalculateCustomFormatScore` method, when the
   case includes a profile score.

The workers also instantiate the upstream Custom Format specification classes.
They do not copy regular expressions or matching rules into CF-Tester.

## Case-supplied state

Radarr and Sonarr normally get some state from a database, an indexer, or an
earlier pipeline step. A test case supplies the state that affects the Custom
Format under test:

- `sizeBytes`
- `indexerFlags`
- `languages`, when the indexer supplies languages
- `mediaTitle`, `year`, and `originalLanguage`
- `episodeTitles`, for Sonarr episode-title language removal
- file paths and scene names for local and stored files

When `originalLanguage` is present, the worker creates the smallest movie or
series fixture and runs the upstream language augmenter. When `languages` is
present, the augmenter treats it as indexer-supplied language data.

## Quality profile scores

Radarr and Sonarr do not read the Guides `trash_id`, `trash_scores`, or
`trash_score_set` fields. CF-Tester converts them into the numeric Custom
Format IDs and scores that the applications store in a quality profile.

The worker starts with the profile's mandatory `formatItems` and adds the
case's `additionalFormats`. It uses the selected `trash_score_set`, then the
CF's `default` score, then `0`. It constructs the upstream quality profile type
and passes the matched Custom Formats to the upstream score method.

CF-Tester does not select CF groups. A score case lists optional Custom Formats
directly so that the tested profile is clear.

## What CF-Tester Does Not Model

CF-Tester does not model:

- indexer-specific multi-language settings
- movie, series, episode, or scene mapping lookups
- CF group choices or minimum score rules
- queue state, blocklists, download clients, or import decisions
- naming templates or file-system operations

These exclusions are deliberate. They do not change the meaning of a Custom
Format match for the supplied input, but they can change an application-level
grab or import decision.

## Result contract

Exit code `0` means all match, non-match, and profile score assertions passed.
Exit code `1` means at least one assertion failed. Exit code `2` means the
input or runner setup was invalid.

Failure diagnostics call each upstream specification's `IsSatisfiedBy` method.
They explain the mismatch. Match results come from the full upstream
calculation service. Profile scores come from the upstream quality profile
method.
