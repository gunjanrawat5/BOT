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
    (folder / 'replay_trace.json').write_text(json.dumps({'seed': seed, 'actions': [action for r in trace for action in ([{'frames': 1, 'buttons': r['action']['buttons']}] * r['action']['frames'] if r.get('frame_by_frame') else [r['action']])]}, indent=2), encoding='utf-8')
    (folder / 'bug.md').write_text(f'# {report["title"]}\n\nStatus: **{status}**\n\n'
        f'Bug ID: {bug_id}\n\nSuspected severity: medium\n\nGame: {game_url}\n\nSeed: {seed}\n\n'
        f'Expected: {report["expected_behavior"]}\n\nActual: {report["actual_behavior"]}\n\n'
        f'Replay successes (of {verification["attempts"]}): {verification["successes"]}\n\n'
        '![Original failure](evidence.png)\n\n## Exact replay\n\n```json\n'
        + json.dumps([r['action'] for r in trace], indent=2) + '\n```\n\n'
        'Full before/after public states and verification details: [bug.json](bug.json).\n', encoding='utf-8')
    return folder


def write_ai_report(root, *, seed, game_url, trace, before, after,
                    before_screenshot, screenshot, assessment, model, video_pending=False):
    """Save every model-flagged issue, including uncertain design assumptions."""
    bug_id = uuid4().hex[:8]
    folder = Path(root) / f'{datetime.now(timezone.utc):%Y%m%dT%H%M%S_%fZ}_{bug_id}'
    folder.mkdir(parents=True)
    finding = assessment.model_dump()
    actions = [action for r in trace for action in (
        [{'frames': 1, 'buttons': r['action']['buttons']}] * r['action']['frames']
        if r.get('frame_by_frame') else [r['action']])]
    report = {'bug_id': bug_id, 'title': assessment.title, 'status': 'suspected',
              'detection_method': 'gemini_assessment', 'model': model,
              'suspected_severity': assessment.severity, 'confidence': assessment.confidence,
              'seed': seed, 'game_url': game_url, 'assessment': finding,
              'expected_behavior': assessment.expected_behavior, 'actual_behavior': assessment.actual_behavior,
              'before': before, 'after': after, 'trace': trace,
              'verification': {'verified': False, 'attempts': 0, 'reason': 'AI judgment; no independent verification or replay performed.'},
              'event_timeline': 'events.json',
              'evidence': {'before': 'before.png', 'after': 'evidence.png'}}
    if video_pending:
        report['video'] = {'file': 'evidence.webm', 'status': 'pending', 'kind': 'original_gameplay_recording'}
    (folder / 'events.json').write_text(json.dumps({'actions': [{'index': i + 1, 'timeline': record.get('timeline', {'available': False, 'complete': False, 'events': []})} for i, record in enumerate(trace)]}, indent=2), encoding='utf-8')
    (folder / 'before.png').write_bytes(before_screenshot)
    (folder / 'evidence.png').write_bytes(screenshot)
    (folder / 'bug.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (folder / 'replay_trace.json').write_text(json.dumps({'seed': seed, 'actions': actions}, indent=2), encoding='utf-8')
    evidence = '\n'.join(f'- {item}' for item in assessment.evidence)
    (folder / 'bug.md').write_text(
        f'# {assessment.title}\n\nStatus: **suspected — AI finding, not independently verified**\n\n'
        f'Bug ID: {bug_id}\n\nModel: {model}\n\nSuspected severity: {assessment.severity}\n\n'
        f'Confidence: {assessment.confidence}\n\nGame: {game_url}\n\nSeed: {seed}\n\n'
        f'## Expected behavior\n\n{assessment.expected_behavior}\n\n'
        f'## Actual behavior\n\n{assessment.actual_behavior}\n\n'
        f'## Observations\n\n{evidence}\n\n## Reasoning and uncertainty\n\n{assessment.reasoning}\n\n'
        f'## Suggested follow-up\n\n{assessment.follow_up}\n\n'
        '## Screenshots\n\n![Before action](before.png)\n\n![After action](evidence.png)\n\n'
        '## Original action batches\n\n```json\n' + json.dumps([r['action'] for r in trace], indent=2)
        + '\n```\n\nExact API calls are in [replay_trace.json](replay_trace.json). '
        'Per-action event evidence: [events.json](events.json). Reproduction has not been attempted. Full states and assessment: [bug.json](bug.json).\n', encoding='utf-8')
    return folder
