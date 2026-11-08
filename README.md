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

## Game profiles

Choose **Space Shooter** or **Ping Pong** in the dashboard's Game selector. It
updates the suggested URL and sends the selected profile to the agent. Configure
the default with `GAME_PROFILE=space_shooter` or `GAME_PROFILE=ping_pong`, and set
`GAME_URL` / `GAME_REPO` for that default. Optional `SPACE_SHOOTER_REPO`,
`PING_PONG_REPO`, `SPACE_SHOOTER_URL`, and `PING_PONG_URL` override each preset.

| Game | Default URL | Logical buttons | Public state |
| --- | --- | --- | --- |
| Space Shooter | `http://localhost:5173` | `left`, `right`, `shoot` | Player health, score, enemies, projectiles |
| Ping Pong | `http://localhost:5174` | `up`, `down`, `pause` | Player/CPU paddles and scores, ball, rally, serve countdown, winner |

For CLI runs, use `uv run spaceqa --game space_shooter` or
`uv run spaceqa --game ping_pong`. An explicit selection uses that game's default
URL (or `SPACE_SHOOTER_URL` / `PING_PONG_URL` override), avoiding a stale `GAME_URL`.
Without `--game`, the CLI uses `GAME_PROFILE` and `GAME_URL` from the environment.

Pong planning uses at most 12 frames per decision and receives an approximate
interception point from public ball position/velocity, including wall rebounds.
Gemini chooses the controls and uses observed paddle movement to avoid overshooting.
This increases model calls per simulated second; use a larger action budget for rallies.
The prediction assists play only and does not decide whether behavior is a bug.

Pong's pause is a once-per-batch toggle. Animated playback and exported traces
send it only on the first frame, preserving the game's batched semantics. Shooter
controls remain unchanged. Pong's raw internal RNG, seed bookkeeping and event
buffer are stripped before model input; width/height become the canonical canvas
size. The separate ordered event API remains the source of action timelines.

Pong planning and review use rally, intercept, serve, scoring and pause concepts,
without fictional shooter health/enemies. Stats record the player's score, keep
CPU points in observation history, and can be filtered by game. New reports keep
their game profile so fixing an old shooter report still targets the shooter repo,
even when Pong is selected. Repair supports Vite games and dependency-free Node
games using `node --test`; games without a build script get JavaScript syntax
checks instead. Run the integration check for the configured default with:

```sh
uv run spaceqa --control-check --headless
```

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
- Toggle **Findings / Stats** to view completed QA sessions, elapsed time, action
  count, final game score, best observed score and links to saved findings.

Only one dashboard-launched run is allowed at a time. Start runs here instead of
launching a second agent from another terminal. Run metadata and redacted output
are saved under `reports/dashboard-runs/<id>/`; the live activity panel displays
the latest 2,000 lines from the current dashboard process. Reports persist across
dashboard restarts. The dashboard remains local to this computer and exposes no
API key or editable shell commands to the browser. Press Ctrl+C in its terminal
to stop the dashboard and its active run.

### Completed-run stats

The Stats table includes successfully completed QA sessions (including sessions
that stop early after finding a bug). Running, stopped and failed sessions and
repair jobs are excluded. Each row represents a whole QA session, which may
include multiple game resets. **Final score** is the score at the last observed
state; **Best score** is the highest observed score across games in that session.
Elapsed time includes planning, gameplay, review and recording finalization.

New dashboard runs persist `summary.json` beside their `run.json` and activity
log. New CLI runs persist summaries under `reports/run-stats/`. Scores are saved
from actual game state, rather than inferred from terminal output. These summaries
remain available after restarting the dashboard. The endpoint is `GET /api/stats`.

For older completed dashboard runs, stats recover duration from saved start/end
times and use activity/session logs and reports for observed scores/action counts.
Missing values display **—**, never a fabricated zero. Prior CLI sessions without
a completion record cannot reliably be classified as completed and are not listed.

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

The current repair adapter supports Node games with `"test": "node --test"`.
For `"build": "vite build"` it uses the game's installed Vite executable; games
without a build script get `node --check` on their JavaScript source instead. Automated
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
open at the end until Enter is pressed in the terminal. Use `--headless` for background runs. Video capture uses animated frame stepping
even headlessly; use `--no-video` without `--watch` for fast batched stepping. Equivalent module command:
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
- `evidence.webm`: original recorded gameplay, shown in the dashboard player.
- `before.png` and `evidence.png`: before/after screenshots (collapsed when a video is available).
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

## Gameplay video

New QA runs record the browser viewport by default. When Gemini flags a suspected
bug, the original recording is finalized as `evidence.webm` in that report folder.
The dashboard provides play/pause, seeking, an **Open video** link, and a
**Jump to flagged action** button using approximate wall-clock action timestamps.
Before/after screenshots remain available in a collapsed section and continue to
be used by Gemini. Video bytes are not uploaded to Gemini.

This is the original captured session, including model-thinking pauses and any
game resets, not a separately reproduced or independently verified bug. The saved
action trace still contains only the active game run. Recording requires frame
stepping so movement is visible in the footage; headless runs also advance frames
visibly to the recorder. WebM playback is served locally with byte-range support
for seeking. Older reports without a video retain their screenshot viewer.

Use `--no-video` to disable recording. Control-check runs do not record video.
Temporary footage is discarded for runs without a suspected bug. With
`--keep-open`, press Enter in the terminal to close the recording context and
finalize the clip; until then the report displays a pending-video message.
Dashboard runs close automatically and finalize the recording before completing.
Video recording follows the [Playwright video API](https://playwright.dev/python/docs/videos).

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
