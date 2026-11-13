<p align="center"><img src="plugin/assets/logo.png" alt="RainDragon logo" width="96"></p>

# RainDragon Forecast

**How long will Claude take?** Forecast tells you, from your own past turns.

When you send a prompt, Forecast shows how long turns like it usually take
*you*, as a range. While Claude works, a small clock shows how far along you
are. If Claude has a major outage, you get a storm warning, so you know a slow
turn is not your fault.

It works in Claude Code and, with the Chrome extension, on claude.ai,
chatgpt.com and gemini.google.com. Your history stays on your computer. In
Claude Code it also has a shared model that runs locally, so there is a range
from your first turn (see [The first few turns](#the-first-few-turns)).

☊☋ are old astronomers' symbols for the Dragon's Head and Tail. You'll see
them at the start of every Forecast line.

Made by [RainDragon AI](https://inference.raindragon.ai/?utm_source=raindragon-forecast&utm_medium=readme).

## Why a range, not a single number

Nobody can say exactly how long a turn will take: the same request can take
20 seconds one time and 2 minutes the next. So Forecast does not guess one
number. It looks at your past turns that were like this one and shows the
range that the middle half of them fell in:

```
☊☋ Forecast 40s–3m · half of your similar turns (57)
```

That means: of your 57 similar turns, half took between 40 seconds and 3
minutes. About half of your future turns should land inside the range too,
and you can check that any time with `/forecast:stats`.

## What you'll see

### In Claude Code

When you send a prompt:

```
☊☋ Forecast 40s–3m · half of your similar turns (57)
```

While Claude works, at the bottom of the screen (newer Claude Code versions):

```
☊☋ 0:42 ▕──●─────▏ clear · 40s–3m
```

The dot moves along the bar as the clock runs. If the turn takes longer than
the range:

```
☊☋ 3:20 ▕────────▏● running long · forecast was 40s–3m
```

If Claude has a major incident:

```
☊☋ Storm warning: Claude has a major incident. Expect slower turns.
```

### On claude.ai, chatgpt.com and gemini.google.com

A small bar just above the message box, with the RainDragon logo:

```
[logo] Forecast 20s–1m · 300–800 words · 0:12
```

It shows how long the reply usually takes and how long it usually is, then a
running clock. Each site keeps its own history, so ChatGPT replies never
change your Claude forecast. gemini.google.com support is new and not yet
tested on the live site (see [docs/PROVIDERS.md](docs/PROVIDERS.md)).

### The first few turns

Forecast needs some history before it can say anything useful:

In Claude Code and the Codex / Gemini CLI hooks, Forecast now shows a range
from your **first** turn. It comes from one model shared by everyone (trained
on public coding-agent sessions from 337 people; it holds numbers only), which
reads this prompt's length, this session's earlier turns and, once you have
some, your own recent turns:

```
☊☋ Forecast 20s–2m · half of turns like this
```

On people it never saw, the middle of that range was within 2x of the real
time more often than the per-user lookup (39.8% vs 35.8%), and about half of
turns landed inside it. After 30 turns, Forecast keeps checking both ranges
against your finished turns and shows whichever has been closer for you over
the last 50. Set **Range source** to `local` for the old rule:

| Turns so far | What you see with `local` |
| :- | :- |
| 0–9 | "RainDragon Forecast is learning your pace. Ranges start after 10 turns." (once per session) |
| 10–29 | a wider range (8 in 10 of your similar turns), marked "still learning" |
| 30 and up | the normal range (half of your similar turns), and one "Forecast has your pace" message |

The extension (claude.ai, chatgpt.com, gemini.google.com) still uses the
per-user rule: the shared model was trained on agent turns, not chat replies.

Turns that fail, turns you stop, and turns during a major Claude incident are
left out, so they never stretch your range.

## Install

### Claude Code

You need `python3` installed. Nothing else.

```bash
claude plugin marketplace add raindragon-ai/raindragon-forecast
claude plugin install forecast@raindragon
```

To try it for one session from a copy of this repository instead:
`claude --plugin-dir plugin/`.

Commands you can type:

* `/forecast:stats`: how often the range held on your own turns, your typical
  turn and your longest turn.
* `/forecast:doctor`: version, settings and counts, to paste into a bug
  report. It never includes your prompts, file names or session ids.

### Chrome extension

1. Download `raindragon-forecast-extension-<version>.zip` from the
   [releases page](https://github.com/raindragon-ai/raindragon-forecast/releases) and unzip it.
2. Open `chrome://extensions`, turn on **Developer mode**, click **Load
   unpacked** and pick the unzipped folder.
3. Open claude.ai, chatgpt.com or gemini.google.com and start typing.

### Codex CLI and Gemini CLI

The same script also works with OpenAI's Codex CLI and Google's Gemini CLI.
You get the range when you send a prompt, but no live clock. Setup steps are
in [docs/INSTALL_CLI.md](docs/INSTALL_CLI.md). This has been tested with
sample data only, not yet with a signed-in Codex or Gemini CLI.

### Check a download

Each release has a `SHA256SUMS` file. In the folder with the zips, run:

```bash
sha256sum -c SHA256SUMS        # Linux
shasum -a 256 -c SHA256SUMS    # macOS
```

Every line must say `OK`.

## Upgrading from RainDragon ETA

RainDragon Forecast used to be called RainDragon ETA. To switch:

```bash
claude plugin uninstall raindragon-eta@raindragon
claude plugin install forecast@raindragon
```

**Your history comes with you.** The first time Forecast runs, it copies your
old ETA history across, so it does not start learning from zero. Your old
files are left where they were. Delete them once you're happy (see below).

The commands are now `/forecast:stats` (was `/raindragon-eta:eval`) and
`/forecast:doctor` (was `/raindragon-eta:doctor`). `RAINDRAGON_ETA_OFF=1`
still turns it off, but please use `RAINDRAGON_FORECAST_OFF=1` from now on.

For the Chrome extension, remove the old one on `chrome://extensions` and load
the new one. Removing the old extension deletes its history, because
Chrome stores the history inside the extension.

## Settings

**Claude Code:** run `/plugin configure forecast@raindragon`.

| Setting | What it does | Default |
| :- | :- | :- |
| Enabled | Turn Forecast off without uninstalling it | on |
| Range width | `50`: the middle half of your similar turns. `80`: the middle 8 in 10, wider but misses less often | `50` |
| Show actual time | After each turn, say how long it really took and whether that was inside the forecast | off |
| Check Claude status | Look at status.claude.com to warn you about major incidents | on |
| Mark | `symbol` (☊☋), `RD` if your terminal shows ☊☋ as boxes, or `none` | `symbol` |
| Range source | `global`: the shared model from your first turn, or your own history once it has been closer for you. `local`: only your own history | `global` |
| Compare with RainDragon's server model | Sends each prompt's text (not the conversation) to inference.raindragon.ai and logs its prediction next to yours; see [PRIVACY.md](PRIVACY.md) | off |
| Server model use | `shadow`: log only, keep showing your range. `server` / `blend`: show the server's range or a mix (after 10 turns with an answer; local on any error or after 0.8 s). `off` | `shadow` |

**Chrome extension:** Extensions → RainDragon Forecast → Details → Extension
options. The same settings (the server comparison is claude.ai only), plus
**Copy diagnostics** and **Clear my history**.

## Turn it off, or remove it

| | Turn off (keeps your history) | Remove completely |
| :- | :- | :- |
| Claude Code | set **Enabled** off, or `export RAINDRAGON_FORECAST_OFF=1` | `claude plugin uninstall forecast@raindragon`, then delete `~/.claude/plugins/data/forecast*` and `~/.raindragon-forecast` |
| Codex / Gemini CLI | `"enabled": false` in `~/.codex/raindragon-forecast/settings.json` or `~/.gemini/raindragon-forecast/settings.json` | delete the hook entries, then `~/.codex/raindragon-forecast` or `~/.gemini/raindragon-forecast` |
| Extension | untick **On** in its options | **Remove** on `chrome://extensions` (this also deletes its history) |
| Old RainDragon ETA files | | `~/.claude/plugins/data/raindragon-eta*`, `~/.raindragon-eta`, `~/.codex/raindragon-eta`, `~/.gemini/raindragon-eta` |

When Forecast is off, it shows nothing and records nothing.

## Your privacy

Forecast keeps how long each turn took and rough size buckets (such as
"short prompt"). It never stores your prompts, Claude's replies or file
names, and none of it leaves your computer unless you turn on the server
comparison (off by default; it sends the prompt text). Otherwise the only thing it fetches from the
internet is the public status page (status.claude.com, status.openai.com or
Google's status dashboard), to warn you about incidents. Full details:
[PRIVACY.md](PRIVACY.md). What we do and don't claim about it:
[CLAIMS.md](CLAIMS.md).

## Report a bug

Run `/forecast:doctor` (Claude Code) or click **Copy diagnostics**
(extension options), and paste the output into your report along with what
you saw and what you expected.

## Not made by Anthropic

RainDragon Forecast is an independent project by RainDragon AI. It is not
made, endorsed or supported by Anthropic.

## How it works (for developers)

Plugin: a `UserPromptSubmit` hook starts the clock and shows the range as a
user-only message (nothing is added to Claude's context); `Stop` saves the
duration; `StopFailure` saves it as failed. A plugin mod (`plugin/hooks/live.ts`)
draws the live status line from the turn the hook saved.

Extension: on claude.ai it reads the page's own markers (`assistant-message`
with `data-is-streaming`) to see when a reply starts and ends. On chatgpt.com
it reads a copy of the page's conversation stream for timing, word count and
model. On gemini.google.com it reads page markers (the stop button and the
newest reply). Text is counted and dropped, never stored.

Codex CLI and Gemini CLI: the same script, run with `--host codex` or
`--host gemini` from those CLIs' own hooks, with a separate history each.
See [docs/PROVIDERS.md](docs/PROVIDERS.md) for how each one measures a turn.

"Similar" narrows by prompt size, conversation size and effort or model, and
falls back to broader groups until one has at least 8 turns. Only your most
recent 300 good turns are used. Some internal names (the
`raindragon_eta` Python package, the hook script's file name) keep the old
name, because they never appear on screen.

| Part | Folder | Version |
| :- | :- | :- |
| Claude Code plugin | `plugin/` | 0.2.0 |
| Chrome extension | `extension/` | 0.2.0 |
| Codex CLI and Gemini CLI hooks | `plugin/scripts/` | 0.2.0 |

## Develop

```bash
python3 -m pytest -q tests   # plugin
node --test tests/           # extension (synthetic fixtures in tests/fixtures/)
claude plugin test plugin/   # live status line
python3 release.py           # dist/*.zip + dist/SHA256SUMS
```
