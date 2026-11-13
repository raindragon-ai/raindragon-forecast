# What we say about RainDragon Forecast

Everything written about the product (landing page, README, store listing,
release notes, announcement) comes from this list. Each claim says how it is
measured. If it cannot be measured, it does not go in.

**Name:** RainDragon Forecast, "Forecast" on everyday lines (plugin id
`forecast`, installed as `forecast@raindragon`). Formerly RainDragon ETA. The
full name appears in three places only: the first message of a session while
it is learning, the `/forecast:stats` header and the install listing.

**Mark:** ☊☋ (U+260A U+260B, the Dragon's Head and Tail) starts every line.
The `mark` setting swaps it for `RD` or nothing where a terminal font shows
boxes. Red is for the storm (incident) warning only, yellow for "running
long" only.

**Not Anthropic:** RainDragon Forecast is an independent project by
RainDragon AI. It is not made, endorsed or supported by Anthropic. Say so
wherever the product is listed.

**Made by:** RainDragon AI, with a link to inference.raindragon.ai, in three
places only: `/forecast:doctor` output, the extension options page and
the README. Never inside an estimate or a status warning: the estimate is the
product, and an ad in it would cost trust. Links carry
`utm_source=raindragon-forecast&utm_medium=<doctor|extension|readme>` so visits
can be counted on the site; the tool itself sends nothing.

**One line:** How long turns like this one usually take you, as an honest
range from your own history.

## Claims we can make

1. **It shows a range, and the range is honest about its own size.**
   The middle-half range comes from your own similar past turns, so about
   half of your turns should land inside it.
   *Measured:* `/forecast:stats` (plugin) or the extension's options page
   replays your history in order and reports how often the range held, next
   to how wide it was. Launch target: coverage within a few points of 50%
   (80% for the wide range), reported per user in the beta (RAI-332).

2. **It tells you when it does not know yet.**
   With Range source `local`: no range before 10
   turns; the wide range, marked "still learning", until 30. *Measured:*
   behaviour is fixed by the rule and covered by tests; first run is shown in
   the docs.

2a. **In Claude Code, the CLI hooks and the extension it has a range from the first turn.**
   The default Range source `global` uses one model shared by everyone,
   trained on public coding-agent sessions (337 people, 72K turns).
   *Measured:* 5-fold leave-people-out on that data (tools/train_turn_model.py
   --louo): the range's middle within 2x of the real time 39.8% vs 35.8% for
   the per-user lookup where it shows anything; 50.4% / 80.2% of turns inside
   the 50% / 80% range; 36.3% within 2x on a person's first 10 turns. Do not
   claim a coverage number for any one user from this: on the one long real
   plugin history we have, the global range alone covered 42.7% at nominal
   50%, and the shown range (global, or local once it has been closer) 46.2%.
   None of these numbers are for chat replies: do not claim them for the
   extension. There the first 10 replies are marked "still learning".

3. **It tells you when Claude is having a major problem.**
   It reads status.claude.com and warns only during a MAJOR incident (impact
   major/critical, or a component in major outage) on Claude Code, the Claude
   API or claude.ai, not on unrelated surfaces such as the Console.
   *Measured:* on the reference corpus, turns ran 1.81x longer during major
   incidents (z=+4.5) and 0.91x during minor/degraded ones, so minor ones are
   deliberately not flagged.

4. **Failures do not skew it.**
   Failed turns, stopped turns and turns during an incident are never used.
   *Measured:* covered by tests; visible in `/forecast:doctor` counts.

5. **Your work stays on your machine.** See PRIVACY.md.

6. **On chatgpt.com it learns your ChatGPT pace separately.**
   ChatGPT replies have their own history, never mixed with Claude's, and
   start from zero: "learning your pace" until 10 replies, the wide range
   until 30. *Measured:* separate storage and the first-run rule are covered
   by tests (synthetic stream fixtures). Not yet checked on a live
   signed-in chatgpt.com page; coverage on ChatGPT is not measured. Do not
   claim any ChatGPT coverage number until the options page reports one.

7. **It tells you when ChatGPT's chat is in a major outage.**
   It reads status.openai.com and warns only when the "Conversations"
   component is in a major/full outage. *Measured:* rule covered by tests.
   Unlike claim 3, there is no slowdown evidence for OpenAI incidents yet,
   and status.openai.com's summary lists no incidents (components only), so
   say "major outage of ChatGPT conversations", not "major incident".

8. **On gemini.google.com it learns your Gemini pace separately.**
   Gemini replies have their own history, never mixed with Claude's or
   ChatGPT's, and start from zero, with the same first-run rule.
   *Measured:* separate storage, the first-run rule and the
   ok/failed/stopped/unknown rules are covered by tests on a synthetic page.
   Not checked on a live signed-in gemini.google.com page: the page markers
   are unverified, and if they are wrong it records nothing. Do not claim
   any Gemini coverage number until the options page reports one from real
   use.

9. **It tells you when Google reports a major Gemini outage.**
   It reads Google's status dashboard and warns only for an open Gemini
   incident marked as an outage or high severity. *Measured:* rule covered
   by tests and checked against the live feed's format; no open Gemini
   incident has been seen in it yet, and there is no slowdown evidence for
   Gemini incidents.

10. **In the Codex CLI and Gemini CLI it learns your pace in each CLI
    separately.** Each CLI has its own history (`~/.codex/raindragon-forecast/`,
    `~/.gemini/raindragon-forecast/`), never mixed with Claude Code's or each
    other's, and starts from zero with the same first-run rule (claim 2).
    Failed turns are excluded: in Codex, turns whose rollout log records an
    error; in Gemini, turns that end with an empty reply. Stopped turns are
    dropped. *Measured:* covered by tests with synthetic hook payloads and a
    synthetic rollout file. Not yet run against a signed-in Codex or Gemini
    CLI; coverage in either CLI is not measured.

11. **In the Gemini CLI it warns during a Gemini outage that Google
    reports.** Google's Workspace status dashboard, product "Gemini", open
    incident with impact SERVICE_OUTAGE or severity high only. *Measured:*
    rule covered by tests. That product is the Gemini app, not the Gemini API
    the CLI calls, and there is no slowdown evidence for it, so say "Google
    reports a Gemini outage", never "the Gemini CLI is down" or "major
    incident".

## Do not say

* "accurate", "precise", "predicts", "knows exactly"
* any single-number estimate ("this will take 42s")
* any accuracy percentage other than band coverage reported next to band width
* "within 2x": a rule that ignores every input and multiplies elapsed time by
  a constant maximises it, so it says nothing about quality
* that the claude.ai extension is as good as the Claude Code plugin: it has
  less to go on (no effort level, a noisier page signal)
* that the chatgpt.com or gemini.google.com range is as good as the
  claude.ai one, or anything comparing Claude's, ChatGPT's and Gemini's
  speed: the histories are separate and nothing compares them
* that it has been tested or checked on gemini.google.com: only on a
  synthetic page so far
* that the extension works with the Codex CLI or the Gemini CLI: those are
  plugin hosts, see docs/PROVIDERS.md
* that it shows a live timer in the Codex CLI or Gemini CLI: neither CLI's
  status line can run a command, so the range is shown once, at the prompt
* that it warns about Codex or OpenAI incidents in the Codex CLI: it does not
  (the only feed with the Codex components is undocumented)
* that the Codex/Gemini CLI support is tested live or as good as the Claude
  Code plugin: synthetic payloads only so far, and Gemini has no model level
* anything about other users' speed: every range is from your own history,
  and cross-user error is not measured until the beta

## Positioning (decision needed)

Proposed: **Claude Code plugin first**, claude.ai extension second. The
plugin sees exact turn boundaries and effort; the extension infers them from
the page.

## Not measured yet (say so if asked)

* How well ranges hold for people other than the author: first measured in
  the private beta (RAI-332).
* Whether a pooled starting estimate would help new users before turn 10
  (RAI-329/334).
