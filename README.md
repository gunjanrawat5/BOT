# SpaceQA Agent

Exploratory QA agent for a browser game running in a separate repository.
Gemini chooses constrained experiments from screenshots and public telemetry;
Python checks invariants and verifies issues by replaying the active run.

## Setup

Requires Python 3.11+ and uv.

```sh
uv sync --locked
cp .env.example .env
uv run playwright install chromium
```

If `.env` already exists, edit it rather than copying over your credentials.
Set `GAME_URL` to the separately running game (default `http://localhost:5173`).

## Check the game integration first

```sh
uv run spaceqa --control-check
```

No Gemini credentials required. This resets with `GAME_SEED`, steps 120 frames
holding right, resets, replays, and checks that initial/final public states match
and movement/tick advance. Evidence is saved under `reports/control-check/`.
This must pass on your real game before using the autonomous mode.

The game must expose exactly these methods:

- `window.gameTestAPI.getState()`
- `window.gameTestAPI.reset(seed)`
- `window.gameTestAPI.enableTestMode(true)`
- `window.gameTestAPI.stepFrames(frames, buttons)`

Test mode must pause the normal loop. Reset must preserve test mode and seed
reproducibility. Frame batches accept integers from 1–180 and unique held buttons `left`,
`right`, `shoot`. Reset/step return values are not required; the controller reads
state separately. The page needs a visible canvas.

Telemetry requires `tick`, `status`, `canvas` (width/height), `player`
(x/y/width/height/health/maxHealth/alive), `score`, `enemies`, and `projectiles`.
Player coordinates are the top-left bounding box. Unknown top-level/player fields
are discarded before model input; entity dictionaries should contain only public
telemetry. See `agent/models.py` for validation.

## Run the agent

Set `GEMINI_API_KEY` and an available vision-capable `GEMINI_MODEL` in `.env`.
No model ID is hardcoded. `GEMINI_RPM=15` spaces all request starts (including
retries) by at least 4.25 seconds. Adjust it to your actual model quota. This
pacing applies within one agent process; other processes sharing the same Google
project and recent runs can still consume the quota. Then:

```sh
uv run spaceqa
# Watch smooth frame-by-frame gameplay and keep the final browser open:
uv run spaceqa --watch --keep-open
# Optional background browser:
uv run spaceqa --headless
```

Equivalent: `uv run python -m agent.main`. `MAX_AGENT_STEPS` defaults to 20.
The browser is visible by default. `--watch` advances through the same public
API one frame at a time, yielding about 1/60 second between updates so movement
is visible. The game pauses while Gemini thinks. Replays use the same stepping
mode, and exported watch-mode replay traces contain the exact single-frame calls.
Use `--control-check --watch` to check deterministic replay in this mode.
`--keep-open` waits for Enter in the terminal before closing, including on errors. Each decision gets a PNG screenshot, public
state, the last five experiments with measured deltas, category counts and
cumulative exploration memory. It is asked to aim at enemies, shoot, dodge and
survive while investigating mechanics. Memory tracks score increases, health loss,
game overs, survival ticks, movement-plus-shooting and edges already reached;
these observations guide open gameplay goals rather than scripting actions.
Already-observed outward edge holds and more than two consecutive boundary tests
are rejected and replanned once. If replanning still fails, the run stops safely.
Memory persists across game resets within a CLI session, not across CLI launches.
Completed experiment outcomes and memory summaries are appended to
`reports/sessions/<UTC timestamp>_<id>.jsonl`, including runs without detected bugs.
These logs survive a later planning failure; they are activity records, not verified
bug reports. Gameplay goals do not add new correctness rules: the detector still
only verifies the declared invariants listed below. Invalid output/provider
errors are retried once, then stop the run without executing an invalid action.
Rate-limit errors (429) wait 30 seconds before one final retry; other
service errors wait two seconds before retrying; permanent request errors stop
immediately. Each request has a 60-second timeout. If one model repeatedly returns
503/504, try another accessible vision model; `gemini-3.1-flash-lite` has passed
a live screenshot-and-schema check for this project.
No Gemini tools or code execution are enabled. The SDK integration follows the
[Google Gen AI SDK documentation](https://googleapis.github.io/python-genai/).

Checked rules: horizontal bounds, health range, score monotonicity, movement after
game over, and reset tick/score/full health. Vertical rules and post-death shooting
checks are deferred until the game design and projectile ownership are declared.
Game-over starts a new per-run replay trace; recent exploration history persists.

On a violation, original before/after states and screenshot are retained, then
three seeded replays check each original invariant. A report is verified only
when every reported invariant reproduces 3/3; errors/non-reproduction leave it
unverified. Files under `reports/<UTC timestamp>_<bug id>/` include `bug.json`,
`bug.md`, `evidence.png`, and `replay_trace.json`. Suspected severity is provisionally
medium and should be reviewed by a person. Reports and `.env` are ignored by Git.

## Verification

```sh
uv run python -m unittest discover -s tests -v
```

Tests cover action validation, deterministic checks, matching invariant replay,
report evidence, game-over trace isolation, model retries, and real Chromium
against a small in-page API fixture. They make no paid model calls. Live game and
Gemini integration still require your running game and configured account.
