// SPEC §8 rule 1: real calls go only to allowlisted, consenting residents, within attempt and
// quiet-hour limits (8 AM to 9 PM local unless the alert is Extreme), and only in live mode.
@id("real_call_guarded")
permit(principal, action == Action::"place_checkin_call", resource)
when {
  context.session.mode == "live" &&
  context.session.callee_allowlisted == true &&
  context.session.callee_consented == true &&
  context.session.attempts_last_hour < 3 &&
  ((context.session.local_hour >= 8 && context.session.local_hour < 21) ||
   context.session.alert_severity == "Extreme")
};
