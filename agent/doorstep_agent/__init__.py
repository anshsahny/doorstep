"""Doorstep agent package.

Doorstep is a neighbour check-in agent built with the Strands Agents SDK and
deployed on Amazon Bedrock AgentCore. When a dangerous-weather alert covers a
volunteer group's area, it phones every at-risk neighbour on the list, sorts who
is OK from who isn't, sends a volunteer to the doors that need a knock, and
interrupts the block captain only for the decisions a human must make.

Phase 1 adds the coordinator Graph, agents, tools, hooks, policies and memory.
"""

__version__ = "0.1.0"
