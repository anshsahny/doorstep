// SPEC §8 rule 4: family contact requires the resident's consent.
@id("family_contact_needs_consent")
permit(principal, action == Action::"notify_family", resource)
when { context.session.family_consent == true };
