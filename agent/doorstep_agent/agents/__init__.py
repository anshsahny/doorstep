"""Doorstep's agents, built with the Strands Agents SDK on Amazon Bedrock (Nova 2 Lite).

- alert_assessor: does this alert activate the org's hazard profile? (structured output)
- triage: call waves from deterministic risk scores plus memory notes (tools + structured output)
- checkin_text: the check-in protocol in text form, same script and tools as voice
- classifier: structured CheckinResult with the deterministic red-flag backstop
- dispatcher: acts on a result with policy-guarded tools (Cedar + audit hook)
- persona: a simulated resident on Nova Micro via the Strands Evals ActorSimulator
"""
