// SPEC §8 rule 3: resident details go only to the captain or the volunteer assigned to that
// resident. The brief travels only inside assign_volunteer (to that one volunteer), and only when
// that volunteer is available and within range. High-risk assignments additionally pause for
// captain approval (Strands interrupt, Phase 2).
@id("assign_available_nearby_volunteer")
permit(principal, action == Action::"assign_volunteer", resource)
when {
  context.session.volunteer_available == true &&
  context.session.volunteer_distance_km < 3
};

// Group broadcasts never contain resident details.
@id("broadcast_without_resident_details")
permit(principal, action == Action::"broadcast_to_volunteers", resource)
when { context.input.include_resident_details == false };
