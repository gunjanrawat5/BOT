"""Verify the same invariant, rather than merely any failure."""
from .detector import check, check_reset


def verify(controller, seed: int, trace: list[dict], violations: list[str], attempts: int = 3) -> dict:
    if attempts < 1:
        raise ValueError('At least one replay is required')
    results = []
    for _ in range(attempts):
        try:
            before = controller.reset(seed)
            observed = set(check_reset(before))
            for record in trace:
                action = record['action']
                after = controller.step(action['frames'], action['buttons'])
                observed.update(check(before, after))
                before = after
            results.append({'reproduced': {v: v in observed for v in violations}})
        except Exception as exc:
            results.append({'reproduced': {v: False for v in violations}, 'error': type(exc).__name__})
    successes = {v: sum(r['reproduced'][v] for r in results) for v in violations}
    return {'attempts': attempts, 'successes': successes, 'results': results,
            'verified': attempts >= 3 and all(n == attempts for n in successes.values())}
