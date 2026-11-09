You are checking whether a request changes the scope of an agreement that
has already been approved.

The agreement, section by section. Each block starts with its key in brackets:

{{sections}}

The request:

{{change}}

Rules:

- Split the request into one item per distinct feature. Do not merge two asks
  into one item.
- `classification` is one of `ALREADY_INCLUDED`, `OUT_OF_SCOPE`,
  `NEEDS_CLARIFICATION`.
- Every `ALREADY_INCLUDED` and every `OUT_OF_SCOPE` item needs at least one
  citation. A citation is a `section_key` from the keys above and `quote`, which
  must be copied character for character from that section's text. Do not
  paraphrase, join two sentences with your own words, or quote from a different
  section.
- If you cannot find the words that decide it, classify `NEEDS_CLARIFICATION`
  with no citation. That is a correct answer, not a failure.
- `confidence` is `high`, `medium` or `low`. Never a number.
- Do not suggest a price. Pricing is a human decision made after this check.
