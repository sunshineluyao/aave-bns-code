# Cross-repository RC8 release contract

`release_contract.json` is the machine-readable coordination layer for the paper, dataset, code, and demo repositories.

Validate it with:

```bash
python scripts/validate_release_contract.py
```

The validator checks structure, immutable commit syntax, repository URLs, evidence-boundary presence, and the rule that a release with unresolved blockers cannot claim `READY`. It does not replace clean-room pipeline execution, LaTeX compilation, official Croissant validation, or human scientific review.
