"""Runtime guards built on Strands Agents hooks.

`ModelCallGuard` bounds one agent invocation to a fixed number of model calls. Strands keeps
re-asking the model while a structured-output tool fails validation; with a model that keeps
emitting the same wrong shape that loop never ends. The guard cancels the invocation instead,
so a drill always finishes and the failure is visible in the audit log.
"""

from __future__ import annotations

from strands.hooks import BeforeInvocationEvent, BeforeModelCallEvent, HookProvider, HookRegistry


class ModelCallGuard(HookProvider):
    def __init__(self, max_calls: int = 8) -> None:
        self.max_calls = max_calls
        self.calls = 0
        self.tripped = False

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeInvocationEvent, self.reset)
        registry.add_callback(BeforeModelCallEvent, self.before_model_call)

    def reset(self, event: BeforeInvocationEvent) -> None:
        self.calls = 0
        self.tripped = False

    def before_model_call(self, event: BeforeModelCallEvent) -> None:
        self.calls += 1
        if self.calls > self.max_calls:
            self.tripped = True
            reason = f"stopped after {self.max_calls} model calls in one invocation"
            event.cancel = reason
            ctx = event.invocation_state.get("ctx")
            if ctx is not None:
                ctx.audit.record(
                    actor=f"agent:{event.agent.name}",
                    type="note",
                    resident_id=event.invocation_state.get("resident_id"),
                    reason=f"model-call guard: {reason}",
                )
