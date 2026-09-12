# evals/personas/

Simulated residents for drills and the check-in classification suite (SPEC §12). One directory
per hazard profile (`heat/`), one YAML file per persona. Every persona is fictional; the
`ground_truth` block is hidden from the agents and used only to score results.

Phase 1 ships the 12-persona drill set for heat: 7 OK, 2 needs-help, 2 urgent (one explicit,
one hidden), 1 no-answer. Phase 6 grows it to 40 with the adversarial cases.
