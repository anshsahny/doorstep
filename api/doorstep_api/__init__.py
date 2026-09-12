"""Doorstep's Lambda handlers: authenticate, deduplicate, rate-limit, forward (PLAN Phase 3).

They hold no judgement about incidents. Who may answer a decision, whether it was already
answered, what a choice does: all of that happens in the coordinator on Amazon Bedrock AgentCore
Runtime (Strands Agents), exactly as in a local drill. Standard library and boto3 only.
"""
