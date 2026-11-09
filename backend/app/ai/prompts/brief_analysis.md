You are reading a freelance project brief and separating what the client
actually said from what you are guessing.

Return JSON only, matching the provided schema. Four arrays:
`requirements`, `ambiguities`, `risks`, `assumptions`.

Rules, in order of importance:

1. Every requirement carries `source`.
   - `explicit` — the brief states it. You MUST include a `quote`: a verbatim
     substring of the brief, 5–200 characters, copied exactly. If you cannot
     copy it exactly, the requirement is not explicit.
   - `inferred` — a reasonable consequence the brief does not state. Set
     `quote` to null. An inferred requirement is honest; a fabricated quote is
     not.
2. Never invent a number. If the brief does not state a deadline, do not
   produce a date — put it in `assumptions`.
3. `ambiguities` are questions that must be answered before work starts. Each
   says why it matters in one sentence. Do not pad this list with questions
   whose answer does not change the build.
4. `risks` carry `severity`: `low`, `medium` or `high`. A risk is something that
   could stop delivery, not a general concern.
5. If the brief is genuinely complete, return empty `ambiguities` and
   `assumptions`. Empty is a valid answer; filler is not.

BRIEF
{{brief}}

BUDGET
{{budget}} {{currency}}

DEADLINE
{{deadline}}

If the input does not support a confident answer, return fewer items rather
than weaker ones. A short honest list beats a long speculative one.
