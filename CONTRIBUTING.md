# Contributing

Contributions should preserve traceability from source data to reported output.

1. Open an issue describing the source, schema, and research consequence.
2. Add or update a configuration or query rather than hard-coding addresses and dates.
3. Add tests for every transformation or metric change.
4. Regenerate the demo pipeline and inspect the manifest.
5. Do not merge entity labels without source, confidence, retrieval date, and reviewer status.
6. Do not present synthetic or exploratory outputs as observed evidence.

Pull requests that change a reported metric must document whether the change is a bug fix, definition change, source correction, or sample update.
