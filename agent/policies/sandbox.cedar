// SPEC §8 rule 2: the sandbox never touches real channels. A forbid always beats a permit.
@id("sandbox_never_real_channels")
forbid(
  principal,
  action in [
    Action::"place_checkin_call",
    Action::"assign_volunteer",
    Action::"broadcast_to_volunteers",
    Action::"notify_family"
  ],
  resource
)
when { context.session.mode == "sandbox" && context.session.channel == "real" };
