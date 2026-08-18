# Guides Integration

The validation workflow belongs in the Guides repository. Add these steps only
after the first CF-Tester release exists.

Replace `OWNER` with the account or organization that hosts CF-Tester. Replace
the other placeholders with immutable values. Use the CF-Tester commit SHA for
`uses`. Use the worker release tag and the SHA-256 value of that release's
`cf-tester-manifest.json` file.

```yaml
jobs:
  custom-format-tests:
    name: Custom Formats ${{ matrix.application }}/${{ matrix.channel }}
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        include:
          - application: radarr
            channel: stable
          - application: sonarr
            channel: stable

    steps:
      - name: Checkout Guides
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.2.2

      - name: Test Custom Formats
        id: cf-tests
        uses: OWNER/CF-Tester@CF_TESTER_COMMIT_SHA
        with:
          application: ${{ matrix.application }}
          channel: ${{ matrix.channel }}
          guides-path: .
          worker-release: CF_TESTER_RELEASE_TAG
          manifest-sha256: CF_TESTER_MANIFEST_SHA256
          junit-path: cf-test-results-${{ matrix.application }}-${{ matrix.channel }}.xml

      - name: Upload Custom Format test results
        if: ${{ !cancelled() }}
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
        with:
          name: custom-format-test-results-${{ matrix.application }}-${{ matrix.channel }}
          path: ${{ steps.cf-tests.outputs.junit-path }}
          if-no-files-found: error
```

Keep the existing schema and repository consistency checks. Run CF-Tester after
those faster checks. The action reads Custom Formats, quality profiles, and
cases from the Guides checkout. It does not store a second copy.

The action verifies the manifest against the SHA-256 value stored in the
Guides workflow. It then verifies the selected worker against the checksum in
that manifest. The three pins protect different parts:

- The action commit SHA fixes the action code.
- The worker release tag selects a set of channel workers.
- The manifest SHA-256 value fixes every worker archive, even if a release
  asset is replaced.

Keep stable Radarr and Sonarr as required pull request checks. A separate
scheduled workflow can use the same job for these optional checks:

```yaml
include:
  - application: radarr
    channel: develop
  - application: radarr
    channel: nightly
  - application: sonarr
    channel: develop
```

Do not add the workflow with unresolved placeholders. It would make Guides CI
fail before the CF-Tester repository and release exist.
