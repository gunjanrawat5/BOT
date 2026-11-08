"""Approximate interception advice from public observations, never bug verdicts."""


def interception(state, history):
    ball, paddle = state['ball'], state['player']
    result = {'prediction_is_estimate': True, 'max_action_frames': 12}
    speeds = []
    for outcome in history[-5:]:
        plan = outcome.get('plan', {})
        buttons = set(plan.get('buttons', []))
        frames = plan.get('frames', 0)
        if frames and len(buttons & {'up', 'down'}) == 1 and 'pause' not in buttons:
            speed = abs(outcome.get('movement_y', 0)) / frames
            if speed:
                speeds.append(speed)
    speed = max(speeds, default=None)
    result['observed_paddle_speed_per_frame'] = speed
    if state['status'] == 'paused' or state.get('serveFrames', 0) or ball['vx'] >= 0:
        result['advice'] = 'Resume if paused; otherwise prepare for the return and observe again soon.'
        return result
    frames = max(0, (paddle['x'] + paddle['width'] + ball['radius'] - ball['x']) / ball['vx'])
    low, high = ball['radius'], state['canvas']['height'] - ball['radius']
    span = high - low
    if span <= 0:
        return result
    # Fold the projected trajectory at the visible top/bottom boundaries.
    phase = (ball['y'] + ball['vy'] * frames - low) % (2 * span)
    target = low + min(phase, 2 * span - phase)
    delta = target - (paddle['y'] + paddle['height'] / 2)
    result.update(frames_until_contact=round(frames, 2), predicted_contact_y=round(target, 2),
                  paddle_center_error=round(delta, 2),
                  advice='Aim at predicted contact, not current ball y. Stop moving once aligned; recheck before contact.')
    if speed:
        result['frames_to_align_estimate'] = round(abs(delta) / speed, 2)
    return result
