# Privacy

RainDragon Forecast watches how long your Claude turns (and, with the extension,
your ChatGPT replies on chatgpt.com and Gemini replies on gemini.google.com;
with the CLI hooks, your Codex CLI and Gemini CLI turns) take. That means it runs inside
your sessions, so here is exactly what it does with them.

## What it stores

Only on your machine:

* **Plugin:** `~/.claude/plugins/data/forecast*/` (a text file per turn and a
  few small markers).
* **Codex CLI / Gemini CLI hooks:** `~/.codex/raindragon-forecast/` and
  `~/.gemini/raindragon-forecast/`, the same kind of files as the plugin, one
  folder per CLI, plus your optional `settings.json` there.
* **Extension:** Chrome's local storage for the extension. claude.ai,
  chatgpt.com and gemini.google.com replies are kept in three separate
  lists, plus the name of the
  ChatGPT model you last sent with (for example `gpt-5-thinking`) so it can
  estimate while you type.

For each finished turn it keeps:

| Kept | Example |
| :- | :- |
| When it finished and how long it took | `"t": 1791445010, "dur": 41.2` |
| Prompt size bucket | `short`, `medium` or `long` |
| Conversation size bucket | `small`, `medium` or `large` |
| Effort level (plugin) or model label (extension, Codex CLI) | `high`, `opus 5.5 medium`, `gpt-5-thinking`, `2.5 flash` |
| Reply length in words (extension) | `350` |
| Whether it failed (and, in Codex, the error kind), or ran during an incident | `"ok": true, "incident": false`, `"error": "server_overloaded"` |

Per session, the plugin and CLI hooks also keep a small file in `sessions/`
with numbers only: how many prompts the session has had and the end time and
duration of its last 10 turns (`{"n": 4, "k": 3, "done": [[1791445010, 41.2], ...]}`),
named by the session id like the other markers and deleted after two days.
Each finished turn also stores the global model's range shown for it
(`"glb": [20.1, 95.3]`), so Forecast can tell which range has been closer for you.

The extension keeps the same two ranges on each reply (`"loc"` and `"glb"`)
and which one it showed (`"shown"`). Its "session" (the conversation's reply
count and last 10 reply times) lives in the page's memory only and is never
saved.

The global model (`turn_model.json`, shipped with the plugin and, as
`lib/turn_model_data.js`, with the extension) was trained
offline on public coding-agent sessions. It contains numbers only, no text from
anyone's prompts, and runs on your machine; nothing about your turns is sent
to train or run it.

The plugin also writes one file outside that folder, `~/.raindragon-forecast/data_dir`,
holding only the path of the folder above, so the live status line can find
the turn in progress.

## What the CLI hooks read

The Codex and Gemini CLIs pass each hook a JSON payload. The script reads
the prompt's **length** (not keeping the text), the session id (only to pair
start and end, kept in a marker deleted when the turn ends or after two
days), Codex's turn id and model name, and the **size** of the session file
the CLI names (`transcript_path`), to bucket conversation size. Gemini's
end-of-turn payload includes the reply; the script only checks whether it is
empty.

**Codex session logs.** When a Codex turn ends without its `Stop` hook
(Codex skips `Stop` when a turn errors), the script reads the end of that
session's rollout log, `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, at
your next prompt: only the `task_complete` / `turn_aborted` line for that
turn, and from it only the duration and the error kind (for example
`server_overloaded`), never the error message, prompts or replies. The path
is kept in the turn's in-progress marker until then. Nothing in
`~/.gemini` outside the script's own folder is opened; the session file's
size is read, not its contents.

## What the extension reads on chatgpt.com

To time a ChatGPT reply, a small script runs in the chatgpt.com page and
reads a **copy** of the page's own reply stream (`/backend-api/conversation`)
when you send a new message. In memory it counts your message's characters
and the reply's words, and notes the model name, when the first word
arrived, and whether the reply finished, failed or was stopped. Only those
numbers and the model name are passed to the extension; the text is dropped
as soon as it is counted. It does not change, delay or block the page's
request or reply. It also reads the length (not the text) of the messages on
screen, to bucket conversation size.

## What the extension reads on gemini.google.com

The extension does not read Gemini's network traffic. It reads the page:

* the **number of characters** in the message box, while you type and when
  you send (Enter or the Send button);
* the **label of the model picker** (for example "2.5 Flash"), kept in
  lower case as the model label above;
* whether the **stop button** is on screen and whether the newest reply is
  still marked as loading, to see when a reply starts and ends, and whether
  that reply shows an error;
* the **number of words** in the newest reply when it ends (the thinking
  section and hidden text are skipped), and the **number of characters** of
  the messages on screen, to bucket conversation size.

The text is counted in place and never copied, kept or sent. It also notes
whether you clicked the stop button, so that a stopped reply is not saved.

It does **not** keep your prompts, Claude's, ChatGPT's or Gemini's replies, file names, chat or
session names, project paths or anything you typed. Markers for a turn in
progress are deleted after two days. History is capped at the most recent
1,000 turns.

## What leaves your machine

One thing: a request to a public status page every two minutes at most, to
see whether there is an incident: `status.claude.com/api/v2/summary.json`
for Claude (plugin, and the extension on claude.ai), and
`status.openai.com/api/v2/summary.json` for the extension on chatgpt.com, and
Google's status dashboard, `www.google.com/appsstatus/dashboard/incidents.json`,
for the extension on gemini.google.com and for the Gemini CLI hook (the hook
caches only the Gemini incidents' status fields). The Google request is sent
without cookies (as are the others), so it is not tied to your Google account.
The Codex CLI hook makes no request.
It sends nothing about you or your work, only the request itself. Turn it
off with **Check Claude status** (plugin), **Check Claude, ChatGPT and
Gemini status** (extension) or `"check_status": false` in the CLI folder's
`settings.json`.

Nothing is sent to RainDragon unless you turn on the server comparison
below. There is no telemetry, analytics or crash reporting.

### Optional: compare with RainDragon's server model (off by default)

RainDragon runs a small model (TinyLM) that predicts how long a reply will be
from the prompt's text. It needs the text, so it is never asked unless you
turn on **Compare with RainDragon's server model** (plugin option
`server_send_text`; in the extension, the options checkbox, which also asks
Chrome for permission to reach `inference.raindragon.ai`). Claude Code and
claude.ai only.

With it on, for each prompt, `POST https://inference.raindragon.ai/v1/eta/predict`
carries:

| Sent | Example |
| :- | :- |
| The prompt you typed, first 8,000 characters | `fix the flaky test in parser_test.py` |
| A random id made on your machine for this install | `3f0c2a9e-7b1d-...` |
| Which client and version | `plugin`, `0.2.0` |
| The model name, always the word `claude` | `claude` |
| `share: false`: the server must not keep the text | `false` |

Never sent: the conversation before the prompt, Claude's replies, file
names, paths, the session id, your account. The server uses the text to make
its prediction and drops it; it keeps the numbers (sizes, its prediction, the
real duration) to measure itself. When the turn ends,
`POST /v1/eta/outcome` carries the prediction's id, the same install id, the
turn's duration and whether it failed.

The answer (a number of tokens) is saved in your local history next to the
local range, as `srv_tok`, so `/forecast:eval` (and the extension's options
page) can show which was closer on your own turns. **Server model use**
chooses what you see: `shadow` (default) keeps showing your local range;
`server` or `blend` show the server's range or a mix, only after 10 turns
have a server answer, and fall back to the local range whenever the server
is slow, offline or answers with anything unexpected.

## Diagnostics

`/forecast:doctor` and **Copy diagnostics** print the version, your settings
and turn counts so you can paste them into a bug report. They contain no
content. You choose whether to share them.

## Deleting your data

* Extension: **Clear my history** in its options (clears claude.ai,
  chatgpt.com and gemini.google.com), or remove the extension.
* Plugin: delete `~/.claude/plugins/data/forecast*` and `~/.raindragon-forecast` (and
  uninstall it if you like). Deleting `install_id` there gives you a new random
  id; the server's rows under the old one are no longer linked to you.
* Codex / Gemini CLI: delete `~/.codex/raindragon-forecast` or
  `~/.gemini/raindragon-forecast` (and the hook entries). Codex's own session logs
  are Codex's; the script never writes to them.
