# SpaceQA Agent

A Gemini-driven exploratory QA agent for a browser game in a separate repository.
Gemini plays from screenshots and public telemetry, then reviews each action's
outcome for suspected bugs. Runtime bug detection uses model judgment, not a
predefined Python invariant checklist.

## Setup

Requires Python 3.11+ and uv.

```sh
uv sync --locked
cp .env.example .env
uv run playwright install chromium
```

Edit an existing `.env` instead of overwriting it. Set `GAME_URL` to your running
game (default `http://localhost:5173`), `GEMINI_API_KEY`, and an available vision
model in `GEMINI_MODEL`. `gemini-3.1-flash-lite` has passed a live screenshot/schema
check in this project. Keep keys out of source control.

## Local dashboard

```sh
uv run spaceqa-dashboard
```

Open **http://127.0.0.1:8765**. Keep the game running in its separate repository.
The dashboard uses the existing `.env`; restart it after changing credentials or
model configuration. Use `--port 8766` if the default port is occupied.

From the dashboard you can:

- Start a run with a localhost game URL, action limit, and seed.
- Watch animated gameplay in a separate Chromium window, or turn off “Watch it
  play” to run headlessly.
- Follow live hypotheses, outcomes, Gemini assessments, and provider errors.
- Stop the active run, including its browser process group.
- Browse existing reports, view paired screenshots, and open Markdown, JSON, or
  replay traces. Reports refresh automatically as new findings arrive.

Only one dashboard-launched run is allowed at a time. Start runs here instead of
launching a second agent from another terminal. Run metadata and redacted output
are saved under `reports/dashboard-runs/<id>/`; the live activity panel displays
the latest 2,000 lines from the current dashboard process. Reports persist across
dashboard restarts. The dashboard remains local to this computer and exposes no
API key or editable shell commands to the browser. Press Ctrl+C in its terminal
to stop the dashboard and its active run.

## Repair agent

Select a finding in the dashboard and click **Fix bug**. The repair agent uses the
configured Gemini model to inspect the selected report, screenshots, timelines,
and requested source files from `GAME_REPO` (default `../SpaceShooter`). Relevant
repository contents are sent to Gemini; credentials and `.env` are not copied.

The original game repo is not edited during investigation. The agent snapshots
its current on-disk state, including uncommitted code, into
`reports/repairs/<id>/workspace/`, then:

1. Runs baseline Node tests and a Vite build.
2. Requests source files and reasons about whether the report is a real bug.
3. Proposes a small source change plus a new regression test.
4. Requires the new regression test to fail against the original source.
5. Requires the complete test suite and build to pass with the fix.
6. Saves `fix.patch`, `result.json`, check output and the edited workspace.

The agent may conclude **not a bug**, **not reproduced** (a diagnostic test passes
on the original code), or **inconclusive**, in which case it does
not offer a patch. It has a two-minute total time budget with no iteration limit. It can finish early
when it reaches a conclusion or validates a patch. Request waits, retries, tests
and builds share the remaining budget; expiration saves an inconclusive result
and restores unvalidated edits. Each model
request shares the configured pacing and retry behavior. It cannot modify old
tests, dependencies or credentials and is limited to `src/` changes and new
`tests/*.test.js` / `tests/*.test.mjs` files. No model-generated shell commands
are executed. Generated tests are local executable code, as with normal repo
tests; the snapshot is isolation from your working files, not an OS sandbox.

A **checks passed** candidate can be reviewed in the report panel. **Review patch**
opens the exact diff; **Repair details** includes every check and its output.
Click **Apply fix to game repo** to copy the validated files back. Applying refuses
if a changed original file no longer matches its snapshot or the validated repair
file was modified. Existing unrelated changes remain untouched. The agent does
not commit, merge, deploy or automatically mark the original report resolved.
After applying, run the QA agent again to check the gameplay.

The current repair adapter supports Node games with `"test": "node --test"` and
`"build": "vite build"`, using the game's installed Vite executable. Automated
browser replay of a repair candidate is not implemented; passing tests/builds
are reported as **checks passed**, not a confirmed gameplay fix.

CLI alternative:

```sh
uv run spaceqa-repair --report reports/<report-id>/bug.json --repo ../SpaceShooter
```

Use `--output <new-directory>` to choose the repair workspace. The two-minute
repair budget also applies to CLI runs. The original report is preserved.

## Run

```sh
uv run spaceqa --watch --keep-open
```

The game advances visibly frame by frame, pauses while Gemini reasons, and stays
open at the end until Enter is pressed in the terminal. Omit `--watch` for fast
batched stepping; use `--headless` for background runs. Equivalent module command:
`uv run python -m agent.main`.

`MAX_AGENT_STEPS` defaults to 20. Each action normally uses **two model
requests**: choose an action, then assess its result. Each request may retry once.
Set `GEMINI_RPM` to your actual quota; the example defaults to 15. Request pacing
includes planning, assessment and retries, but only accounts for this process.
429 errors wait 30 seconds before retrying; service errors wait two seconds.
Permanent request errors stop immediately. Each request has a 60-second timeout.

## How it tests

1. Capture the current screenshot and public game state.
2. Ask Gemini to choose one validated action (buttons + frame count).
3. Execute that action and capture the after screenshot/state.
4. Read the public event timeline for the completed action, if supported, and ask
   Gemini to assess the paired observations and recent history for a likely
   bug, describing expected/actual behavior, evidence, confidence and uncertainty.
5. If Gemini flags an issue, save a suspected-bug report and stop. Otherwise keep
   exploring until the step limit or a provider error.

Gemini is asked to aim, fire, steer, avoid hazards and investigate interactions.
Compact memory tracks score changes, health loss, deaths and previous exploration
across game resets in the same CLI session. Observations are not labeled as kills
or confirmed mechanics merely because an enemy disappeared. Repeated outward
edge holds and long boundary-only streaks are replanned. Action/schema validation
still constrains what the model can execute; it does not decide whether gameplay
is correct. No shell/code tools are exposed to Gemini.

The reviewer can report issues outside previously known rules, including suspected
combat rewards or collision inconsistencies. It must separate observations from
assumptions and note ambiguities. It can miss real bugs or produce false positives.
There is no model-confidence threshold vetoing a flagged issue.

## Reports and activity

Every model-flagged issue creates `reports/<UTC timestamp>_<id>/` containing:

- `bug.md`: expected/actual behavior, evidence, reasoning and follow-up.
- `bug.json`: full assessment, public states and per-run action trace.
- `before.png` and `evidence.png`: before/after screenshots.
- `replay_trace.json`: seed and exact original API calls.
- `events.json`: per-action event timelines and completeness metadata.

Reports are marked **suspected**, with `verification.verified=false` and zero
replay attempts. No deterministic replay verification is performed in this mode;
a saved trace is not proof that the issue reproduces. Watch-mode traces expand
batches into the actual single-frame calls. Reports stop the run at the first
suspected bug; with `--keep-open`, the final game view remains available.

Completed actions are appended before assessment to
`reports/sessions/<UTC timestamp>_<id>.jsonl`; assessments are appended separately.
A provider failure does not erase earlier records. Logs include runs with no bugs.
Memory is retained across game resets, not across separate CLI launches. All
reports and `.env` are ignored by Git.

## Game contract

The page needs a canvas and these methods on `window.gameTestAPI`:

- `getState()`
- `reset(seed)`
- `enableTestMode(true)`
- `stepFrames(frames, buttons)`

Test mode pauses the normal game loop. Reset preserves test mode and seeded
reproducibility. Frame batches accept integers 1–180 and unique held buttons
`left`, `right`, `shoot`. Reset/step return values are not required; the controller
reads state separately.

Telemetry requires `tick`, `status`, `canvas` (width/height), `player`
(x/y/width/height/health/maxHealth/alive), `score`, `enemies`, and `projectiles`.
Player coordinates are the top-left bounding box. Unknown top-level/player fields
are discarded; entity dictionaries should expose only public observations.

For integration diagnostics without Gemini:

```sh
uv run spaceqa --control-check --watch
```

This separate diagnostic checks startup, movement and seeded state equality. It
still uses reset sanity checks, but those do not participate in autonomous bug
detection. Legacy invariant/replay helpers remain available for future optional
verification; the autonomous loop does not invoke them.

## Public event timeline

The game can expose `gameTestAPI.getEvents(afterSequence)` to provide events
between screenshots. The controller automatically reads it, resets the cursor on
new runs, and validates ordering, continuity and retention markers. It forwards the
per-action timeline to Gemini and saves it in activity logs and bug reports. The
dashboard includes an event-timeline link for new reports.

See [the complete game contract](docs/GAME_EVENT_CONTRACT.md) for the exact method,
envelope, event types and reset semantics. The sibling SpaceShooter repository is
instrumented at gameplay mutation sites. Events include hits, kills, spawns,
actual score changes, health loss, damage blocked by invulnerability, and offscreen
removals. The event API does not expose defect flags or label bugs.

Existing games without `getEvents` still run. Gemini sees an explicit unavailable
or incomplete marker, so missing events cannot be interpreted as proof that
nothing happened. Structured events supplement screenshots; intermediate image
capture and automatic investigation/replay are not added by this contract.

## Tests

```sh
uv run python -m unittest discover -s tests -v
```

Tests cover AI-generated reports without invariant violations, no deterministic
overrides of model judgments, assessment evidence/context, partial activity logs,
frame stepping, planning memory, validation and retry behavior. Unit tests make
no paid model calls. The browser integration test uses a small Chromium fixture.

The integration uses the [Google Gen AI SDK](https://googleapis.github.io/python-genai/).
