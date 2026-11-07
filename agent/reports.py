"""Persist original evidence before replay can change browser state."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def write_report(root, *, seed, game_url, trace, violations, before, after, screenshot, verification):
    bug_id = uuid4().hex[:8]
    folder = Path(root) / f'{datetime.now(timezone.utc):%Y%m%dT%H%M%S_%fZ}_{bug_id}'
    folder.mkdir(parents=True)
    status = 'verified' if verification['verified'] else 'unverified'
    last = trace[-1]['plan'] if trace else {}
    report = {'bug_id': bug_id, 'title': ', '.join(violations), 'suspected_severity': 'medium',
              'status': status, 'seed': seed, 'game_url': game_url, 'violations': violations,
              'expected_behavior': last.get('expected_behavior', 'Reset starts at tick 0, score 0, full health, within bounds.'),
              'actual_behavior': f'Public state violated: {", ".join(violations)}',
              'trace': trace, 'before': before, 'after': after, 'verification': verification,
              'evidence': 'evidence.png'}
    (folder / 'evidence.png').write_bytes(screenshot)
    (folder / 'bug.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (folder / 'replay_trace.json').write_text(json.dumps({'seed': seed, 'actions': [r['action'] for r in trace]}, indent=2), encoding='utf-8')
    (folder / 'bug.md').write_text(f'# {report["title"]}\n\nStatus: **{status}**\n\n'
        f'Bug ID: {bug_id}\n\nSuspected severity: medium\n\nGame: {game_url}\n\nSeed: {seed}\n\n'
        f'Expected: {report["expected_behavior"]}\n\nActual: {report["actual_behavior"]}\n\n'
        f'Replay successes (of {verification["attempts"]}): {verification["successes"]}\n\n'
        '![Original failure](evidence.png)\n\n## Exact replay\n\n```json\n'
        + json.dumps([r['action'] for r in trace], indent=2) + '\n```\n\n'
        'Full before/after public states and verification details: [bug.json](bug.json).\n', encoding='utf-8')
    return folder
