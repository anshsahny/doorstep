// SPEC §8 rule 6: read-only tools are always allowed, plus the side-effect-free helpers the
// spec lists as "always permitted" (timers, resident tips, escalation, closing a case).
@id("read_only_always")
permit(
  principal,
  action in [
    Action::"get_org_profile",
    Action::"get_roster",
    Action::"score_residents",
    Action::"get_resident_memory",
    Action::"find_relief_centres",
    Action::"find_nearest_volunteers",
    Action::"schedule_recheck",
    Action::"send_resident_tip",
    Action::"escalate_to_captain",
    Action::"close_case"
  ],
  resource
);

// Simulated check-ins only exist in drills and the sandbox (SPEC §7).
@id("simulated_checkins_outside_live")
permit(principal, action == Action::"start_simulated_checkin", resource)
when { ["drill", "sandbox"].contains(context.session.mode) };
