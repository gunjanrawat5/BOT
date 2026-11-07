"""Deterministic checks; no model opinions are treated as defects."""


def check_state(state: dict) -> list[str]:
    player, canvas = state['player'], state['canvas']
    issues = []
    if player['x'] < -1e-6 or player['x'] + player['width'] > canvas['width'] + 1e-6:
        issues.append('horizontal_boundary_violation')
    if not 0 <= player['health'] <= player['maxHealth']:
        issues.append('invalid_health_range')
    return issues


def check_reset(state: dict) -> list[str]:
    issues = check_state(state)
    if state['tick'] != 0 or state['score'] != 0 or state['player']['health'] != state['player']['maxHealth']:
        issues.append('reset_correctness_violation')
    return issues


def check(before: dict, after: dict) -> list[str]:
    issues = check_state(after)
    if after['score'] < before['score']:
        issues.append('score_decreased')
    if before['status'] == 'game_over' and any(before['player'][k] != after['player'][k] for k in ('x', 'y')):
        issues.append('movement_after_game_over')
    # Projectile ownership/identity is not declared by the API, so shot detection
    # cannot safely distinguish new player shots from existing/enemy projectiles.
    return issues
