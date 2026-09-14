# evals/

The four eval suites (SPEC §12), run with the Strands Evals SDK: `make evals`.

- `suite_checkin.py`: 40 simulated residents (`personas/heat`, `personas/heat-eval`)
- `suite_dispatcher.py`: 12 dispatcher trajectory scenarios
- `suite_redteam.py`: forced and injected attacks on Cedar and the tools
- `suite_backtest.py`: the June 2021 alert against all 48 residents (`personas/heat-backtest`)

Scoring is deterministic (`scoring.py`, tested in `tests/test_eval_scoring.py`). Results land in
`results/`, the report in `REPORT.md` and `report.json` (also copied to the dashboard).
