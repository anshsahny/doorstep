// SPEC §8 rule 5: emergency calls are recorded by humans only. Doorstep never contacts emergency
// services itself; it tells the resident to call 911 and pages a human. The agent principal
// (role "agent") never matches this permit, so its attempts are denied and audited.
@id("emergency_calls_recorded_by_captain_only")
permit(principal, action == Action::"record_emergency_call", resource)
when { context.session.role == "captain" };
