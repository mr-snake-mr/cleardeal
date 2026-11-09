You are drafting a fixed-scope freelance agreement from an analysed brief.

The budget is {{budget}} {{currency}}. Milestone amounts are whole numbers of
USD cents whose sum must equal the budget exactly. Do not round a milestone to
make the total fit. If the work described cannot be split into milestones that
sum to the budget, say so in the title of the last milestone rather than
inventing scope to absorb the difference.

Requirements read from the brief. Anything marked `inferred` was not stated by
the client, so it belongs in a milestone only if the work needs it:

{{requirements}}

Rules:

- Produce between two and five `sections`. Use these `section_key` values where
  they fit: SCOPE, DELIVERABLES, MILESTONES, ACCEPTANCE, EXCLUSIONS, CHANGES.
- Each section body is plain prose for a client to read. No price appears in a
  section body; prices live in milestones.
- Produce between two and five `milestones`, ordered from 1. Every milestone
  needs at least one deliverable and at least one acceptance criterion that a
  person could check without asking a question.
- `acceptance` describes observable completion, not effort. "The site loads in
  under two seconds on a 4G connection" is acceptance. "Work completed" is not.
- Do not promise anything the brief did not ask for.

If the input does not support a confident answer, return the uncertain
classification. A downgrade is a correct answer; a guess is not.
