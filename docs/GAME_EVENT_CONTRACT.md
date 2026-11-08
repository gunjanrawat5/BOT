# Public gameplay event contract v1

This supplements the existing `window.gameTestAPI` methods. The game owns and
emits telemetry; the agent reads it. Events record actual observable simulation
transitions. They do not contain bug flags, source code, verdicts, expected rewards,
or inferred kills. Gemini continues to decide whether behavior is suspicious.

## API

```js
window.gameTestAPI.getEvents(afterSequence = 0)
```

Read-only, synchronous, does not advance time, consume events, or change RNG.
`afterSequence` is an integer >= 0. Return all retained events with
`sequence > afterSequence`, in strictly ascending order:

```json
{
  "version": 1,
  "events": [
    {"sequence": 11, "tick": 534, "type": "projectile_hit", "data": {"projectile_id": "p8", "owner": "player", "target_id": "e7"}},
    {"sequence": 12, "tick": 534, "type": "enemy_destroyed", "data": {"enemy_id": "e7", "reason": "player_projectile", "projectile_id": "p8"}},
    {"sequence": 13, "tick": 534, "type": "score_changed", "data": {"before": 0, "after": 100, "delta": 100, "reason": "enemy_destroyed", "enemy_id": "e7", "cause_sequence": 12}}
  ],
  "nextSequence": 13,
  "truncated": false
}
```

`nextSequence` is the latest emitted sequence (0 if none), NOT the next unused
number. Use it as the cursor on the next read. Multiple events can share a tick;
sequence disambiguates their order. An action timeline includes only events after
the pre-action cursor through the post-action tick. Read until current, including
any events emitted inside one large `stepFrames` call.

## Reset, retention and reproducibility

- Each reset clears the buffer and restarts sequences at 1, emits `game_reset` at
  tick 0, and preserves test mode. The agent resets its cursor after every reset.
- Entity IDs are unique within a run; do not correlate the same ID across resets.
- Retain the latest 4,096 events. `truncated=true` means requested events have
  already been evicted. The agent must mark the timeline incomplete; missing
  events then cannot be used as evidence that something did not happen.
- Reject a cursor greater than the latest sequence: it belongs to a different
  run or invalid reader state. Reject non-integer and negative cursors.
- Reads return detached copies. No mutation of the returned arrays/objects can
  affect gameplay. No reading side effects, wall-clock timestamps, random UUIDs,
  RNG consumption, or event changes based on renderer/watch/headless mode.
- Same seed + actions yields the same states AND event sequence.

## Game profiles

The envelope/cursor contract is shared by Space Shooter and Ping Pong. Events and
public state fields are game-specific. Space Shooter inputs remain `left`, `right`,
`shoot`. Ping Pong inputs are `up`, `down`, `pause`; pause toggles once per batch,
on its first simulation frame, and is removed from later single-frame calls in
animated mode. An empty button list waits. Both use 1–180 frames.

Pong state includes player/CPU paddles, a ball, player/CPU point totals, winning
score, rally count, serve countdown, and an optional winner. It has no health,
enemy or shooting fields. The controller retains only these public fields and
normalizes width/height to canvas size; it discards RNG and the internal event
buffer. Pong event types include `game_reset`, `game_started`, `ball_served`,
`wall_hit`, `paddle_hit`, `score_changed`, `pause_changed`, and `game_over`.
`score_changed` carries `scorer` (player/cpu), before, after and delta. Unlike
Shooter's reward events it has no enemy ID/cause sequence. Data fields must be
interpreted in the active game's context. Reports record the profile for replay
and repair routing. The table below describes Space Shooter event payloads.

## Events

All records require `sequence`, `tick`, `type`, and a public `data` object.
Use IDs from public state and identify player projectiles vs enemy projectiles.

| Type | Data | Emit when |
| --- | --- | --- |
| `game_reset` | `seed` | New run created |
| `enemy_spawned` | `enemy_id`, `x`, `y` | Enemy enters the simulation |
| `projectile_spawned` | `projectile_id`, `owner`, `source_id`, `x`, `y` | Actual projectile creation |
| `projectile_hit` | `projectile_id`, `owner`, `target_id` | Actual overlap/hit handling |
| `enemy_destroyed` | `enemy_id`, `reason`, optional `projectile_id` | Enemy removed by combat or contact |
| `entity_left_playfield` | `entity_id`, `entity_type` | Removal for leaving bounds; not a kill |
| `score_changed` | `before`, `after`, `delta`, `reason`, optional `enemy_id`, `cause_sequence` | Actual score mutation; never emit a reward that did not occur |
| `player_damaged` | `before`, `after`, `delta`, `source_id`, `source_type` | Actual health mutation |
| `damage_blocked` | `source_id`, `source_type`, `reason`, `remaining_frames` | Actual collision where invulnerability suppresses damage |
| `game_over` | `health` | First transition to game over |

Instrument the real mutation sites, not a secondary expected-behavior simulation.
For example, if a defect suppresses scoring, `enemy_destroyed` still exists but
`score_changed` does not. Record actual values and observed causes. A collision
blocked by invulnerability must be distinguished from a missed collision.

## Public state

`getState()` may also include `enemyProjectiles`, with public projectile IDs and
bounding boxes. The agent preserves this field when supplied; older games may
omit it. Ownership is explicit in projectile events.

## Agent behavior

The agent auto-detects `getEvents`. Existing games without it still work, but the
review explicitly marks temporal evidence unavailable. Invalid or truncated
telemetry is marked incomplete, never silently treated as an empty complete log.
Structured events are sent to Gemini alongside before/after screenshots and states
and retained in session logs and report action traces. A report additionally
exports `events.json`, preserving per-action completeness and ordering.

This contract adds event telemetry only. It does not add intermediate screenshots
or automatic follow-up/replay verification. Those can be layered on afterward.
