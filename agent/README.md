# agent/

The `doorstep_agent` package, built with the Strands Agents SDK: the incident Graph
(`graph.py`), the agents (`agents/`: alert assessor, triage, text check-in, classifier,
dispatcher, simulated personas), tools (`tools.py`), hooks (`audit.py`, `approvals.py`,
`guards.py`), decisions and resume (`decisions.py`, `sessions.py`), the stores (in memory and
DynamoDB) and the cloud coordinator (`cloud/`). Hazard profiles live in `doorstep_agent/profiles/`;
Cedar policies in `policies/` with a plain-English version in `policies/plain_english.yaml`.
Resident notes come from the roster (AgentCore Memory is roadmap, not built).
