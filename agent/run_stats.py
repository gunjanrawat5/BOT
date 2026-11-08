"""Persistent completed-run statistics, with conservative legacy recovery."""
import json
import math
from pathlib import Path
import re
import time


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_run_summary(reports, result, *, seed, game_url, started, elapsed, dashboard_id=None):
    root = Path(reports)
    run_id = dashboard_id or Path(result['session']).stem
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', run_id):
        raise ValueError('Invalid run summary ID')
    path = root/'dashboard-runs'/run_id/'summary.json' if dashboard_id else root/'run-stats'/f'{run_id}.json'
    stats = result['stats']
    report_id = Path(result['report']).name if result.get('report') else None
    data = {'id': run_id, 'kind': 'exploration', 'status': 'completed',
            'started': started, 'ended': time.time(), 'duration_seconds': elapsed,
            'seed': seed, 'game_url': game_url, **stats,
            'outcome': 'finding_saved' if report_id else 'no_findings',
            'report_id': report_id, 'session': str(result['session'])}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, indent=2))
    temporary.replace(path)
    return path


def legacy_details(reports, folder):
    values = {'actions': None, 'final_score': None, 'best_score': None, 'outcome': 'unknown', 'report_id': None}
    try:
        lines = (folder/'activity.log').read_text().splitlines()
    except OSError:
        return values
    pointers = [line.split('INFO Session observations: ', 1)[1] for line in lines if line.startswith('INFO Session observations: ')]
    if pointers:
        session = Path(pointers[-1])
        if not session.is_absolute():
            session = reports.parent/session
        session = session.resolve()
        if session.parent == (reports/'sessions').resolve() and session.suffix == '.jsonl':
            try:
                with session.open() as stream:
                    actions = 0
                    for line in stream:
                        try: row = json.loads(line)
                        except ValueError: continue
                        if not isinstance(row, dict): continue
                        # Early logs did not have an event discriminator.
                        if row.get('event') in (None, 'action') and isinstance(row.get('outcome'), dict):
                            actions += 1
                            outcome = row['outcome']
                            values['final_score'] = number(outcome.get('score'))
                            best = number(row.get('memory', {}).get('best_score'))
                            if best is not None: values['best_score'] = best
                    if actions: values['actions'] = actions
            except OSError:
                pass
    for line in lines:
        match = re.fullmatch(r'INFO AI suspected bug report: (.+) \(not independently verified\)', line)
        if match:
            report_folder = Path(match.group(1))
            if not report_folder.is_absolute(): report_folder = reports.parent/report_folder
            report_folder = report_folder.resolve()
            if report_folder.parent == reports.resolve():
                report = read_json(report_folder/'bug.json')
                if report:
                    values.update(outcome='finding_saved', report_id=report_folder.name)
                    values['final_score'] = number(report.get('after', {}).get('score'))
        elif re.fullmatch(r'INFO Finished \d+ (actions|experiments);.*(?:Gemini flagged no suspected bugs\.|No checked invariant violated\.)', line):
            values['outcome'] = 'no_findings'
    if values['actions'] is None:
        count = sum(line.startswith('INFO Outcome: ') for line in lines)
        if count: values['actions'] = count
    return values


def completed_runs(reports, cache=None):
    root = Path(reports).resolve()
    rows = []
    cache = cache if cache is not None else {}
    for metadata in (root/'dashboard-runs').glob('*/run.json'):
        if not metadata.resolve().is_relative_to(root): continue
        run = read_json(metadata)
        if run.get('status') != 'completed' or run.get('kind', 'exploration') != 'exploration': continue
        folder = metadata.parent
        summary_path = folder/'summary.json'
        summary = read_json(summary_path) if summary_path.resolve().is_relative_to(root) else {}
        if summary.get('status') == 'completed':
            row = {**run, **summary}
        else:
            log = folder/'activity.log'
            key = (str(folder), metadata.stat().st_mtime_ns, log.stat().st_mtime_ns if log.exists() else None)
            if key not in cache:
                cache[key] = legacy_details(root, folder)
            row = {**run, **cache[key]}
        row['id'] = folder.name
        rows.append(row)
    for summary_path in (root/'run-stats').glob('*.json'):
        if not summary_path.resolve().is_relative_to(root): continue
        row = read_json(summary_path)
        if row.get('status') == 'completed' and row.get('kind') == 'exploration': rows.append(row)
    normalized = []
    for row in rows:
        gameplay = number(row.get('gameplay_seconds'))
        if gameplay is None:
            gameplay = recover_gameplay(root, row)
        start, end = number(row.get('started')), number(row.get('ended'))
        elapsed = number(row.get('duration_seconds'))
        if elapsed is None and start is not None and end is not None: elapsed = end-start
        normalized.append({'id': str(row.get('id', 'unknown')), 'game_profile': row.get('game_profile', 'space_shooter'), 'started': start, 'ended': end,
                           'duration_seconds': max(0, elapsed) if elapsed is not None else None,
                           'gameplay_seconds': gameplay,
                           'actions': number(row.get('actions')), 'max_steps': number(row.get('max_steps')),
                           'final_score': number(row.get('final_score')), 'best_score': number(row.get('best_score')),
                           'seed': number(row.get('seed')), 'outcome': row.get('outcome', 'unknown'),
                           'report_id': row.get('report_id')})
    normalized.sort(key=lambda row: row['ended'] or row['started'] or 0, reverse=True)
    scores = [row['best_score'] for row in normalized if row['best_score'] is not None]
    times = [row['duration_seconds'] for row in normalized if row['duration_seconds'] is not None]
    return {'runs': normalized, 'totals': {'completed': len(normalized), 'duration_seconds': sum(times),
                                         'gameplay_seconds': sum(row['gameplay_seconds'] for row in normalized if row['gameplay_seconds'] is not None),
                                         'best_score': max(scores) if scores else None}}


def recover_gameplay(root, row):
    """Recover simulation time only from recorded actions, never wall duration."""
    session = row.get('session')
    if not session:
        folder = root/'dashboard-runs'/str(row.get('id', ''))
        try:
            lines = (folder/'activity.log').read_text().splitlines()
            session = next((line.split('INFO Session observations: ', 1)[1] for line in lines
                            if line.startswith('INFO Session observations: ')), None)
        except OSError:
            return None
    if not session:
        return None
    path = Path(session)
    if not path.is_absolute(): path = root.parent/path
    if path.resolve().parent != (root/'sessions').resolve(): return None
    frames, found = 0, False
    try:
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record.get('event') not in (None, 'action'): continue
            outcome = record.get('outcome', {})
            plan = outcome.get('plan', {})
            count = number(outcome.get('gameplay_frames'))
            if count is None:
                count = number(plan.get('frames'))
                if count is None: return None
                count = 0 if outcome.get('status') == 'paused' else max(0, count-int('pause' in plan.get('buttons', [])))
            frames += max(0, count)
            found = True
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return frames/60 if found else None
