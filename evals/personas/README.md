# evals/personas/

Simulated residents for drills and the check-in classification suite (SPEC §12). One directory
per hazard profile (`heat/`), one YAML file per persona. Every persona is fictional; the
`ground_truth` block is hidden from the agents and used only to score results.

- `heat/` (12): the drill set used by every drill and the sandbox: 7 OK, 2 needs-help, 2 urgent,
  1 no-answer.
- `heat-eval/` (29): the rest of the classification suite (40 answering personas in all), including
  hidden urgent and 4 adversarial personas whose attack lines are `scripted:` word for word.
- `heat-backtest/` (7): the remaining roster residents, so the 2021 backtest covers all 48.
