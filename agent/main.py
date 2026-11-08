"""CLI for browser control checks and autonomous exploration."""
import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from .memory import ExplorationMemory
from .controller import GameController
from .detector import check_reset
from .reports import write_ai_report
from .video import RunRecording
from .run_stats import save_run_summary
from .profiles import get_profile, player_score


def explore(controller, brain, *, seed, max_steps, game_url, reports):
    history, trace, coverage = [], [], set()
    profile = get_profile(getattr(controller, 'profile', 'space_shooter'))
    memory = ExplorationMemory(profile)
    session = Path(reports) / 'sessions' / f'{datetime.now(timezone.utc):%Y%m%dT%H%M%S_%fZ}_{uuid4().hex[:8]}.jsonl'
    session.parent.mkdir(parents=True, exist_ok=True)
    logging.info('Session observations: %s', session)
    run = 1
    gameplay_frames = 0
    state = controller.reset(seed)
    def stats():
        return {'actions': memory.steps, 'max_steps': max_steps, 'final_score': player_score(state), 'best_score': memory.best_score, 'games_played': run, 'game_profile': profile.id, 'game_name': profile.name, 'gameplay_seconds': gameplay_frames / 60}
    for step in range(max_steps):
        if state['status'] == 'game_over':
            run += 1
            trace = []
            state = controller.reset(seed)
        before = state
        before_image = controller.screenshot()
        plan = brain.choose_next(before_image, state, history[-5:], coverage, memory=memory)
        logging.info('Step %d, run %d: %s (%s, %d frames)', step + 1, run, plan.hypothesis, plan.buttons, plan.frames)
        action = {'frames': plan.frames, 'buttons': plan.buttons}
        recording_started = getattr(controller, 'recording_started', None)
        action_started = time.monotonic()
        state = controller.step(**action)
        active_frames = 0 if state['status'] == 'paused' else max(0, plan.frames - int('pause' in plan.buttons))
        gameplay_frames += active_frames
        action_ended = time.monotonic()
        after_image = controller.screenshot()
        timeline = getattr(controller, 'last_events', None) or {'available': False, 'complete': False, 'events': [], 'reason': 'No event API available'}
        trace.append({'action': action, 'frame_by_frame': getattr(controller, 'watch', False),
                      'game_profile': profile.id, 'pulse_buttons': list(profile.pulse_buttons), 'plan': plan.model_dump(), 'before': before, 'after': state, 'timeline': timeline})
        if recording_started is not None:
            trace[-1]['recording_seconds'] = {'start': round(action_started-recording_started, 3),
                                               'end': round(action_ended-recording_started, 3)}
        outcome = memory.record(before, state, plan, run, timeline=timeline)
        outcome['gameplay_frames'] = active_frames
        outcome['timeline'] = timeline
        history.append(outcome)
        # Persist the completed action before the assessment request can fail.
        with session.open('a', encoding='utf-8') as log:
            log.write(json.dumps({'event': 'action', 'step': step + 1, 'outcome': outcome, 'memory': memory.summary()}) + '\n')
        if profile.id == 'ping_pong':
            logging.info('Outcome: paddle y %g → %g; score %d:%d; rally %d; status %s',
                         before['player']['y'], state['player']['y'], state['score']['player'], state['score']['cpu'], state['rally'], state['status'])
        else:
            logging.info('Outcome: x %g → %g; score %+g; health %+g; enemies %d → %d; status %s',
                         before['player']['x'], state['player']['x'], outcome['score_delta'],
                         outcome['health_delta'], len(before['enemies']), len(state['enemies']), state['status'])
        logging.info('Action events: %d; evidence %s', len(timeline['events']), 'complete' if timeline['complete'] else 'incomplete or unavailable')
        assessment = brain.assess(before_screenshot=before_image, after_screenshot=after_image,
                                  before=before, after=state, plan=plan,
                                  recent_history=history[-5:], memory=memory, timeline=timeline)
        outcome['assessment'] = assessment.model_dump()
        coverage.add(plan.category)
        with session.open('a', encoding='utf-8') as log:
            log.write(json.dumps({'event': 'assessment', 'step': step + 1, 'assessment': assessment.model_dump()}) + '\n')
        logging.info('Gemini review: %s (%s confidence). %s',
                     'suspected bug' if assessment.issue_found else 'no issue flagged',
                     assessment.confidence, assessment.reasoning)
        if assessment.issue_found:
            folder = write_ai_report(reports, seed=seed, game_url=game_url, trace=trace,
                before=before, after=state, before_screenshot=before_image, screenshot=after_image,
                assessment=assessment, model=brain.model, video_pending=recording_started is not None, game_profile=profile.id)
            logging.info('AI suspected bug report: %s (not independently verified)', folder)
            return {'report': str(folder), 'assessment': assessment.model_dump(), 'session': str(session), 'stats': stats()}
    logging.info('Finished %d actions; categories: %s. Gemini flagged no suspected bugs.', max_steps, sorted(coverage))
    return {'steps': max_steps, 'coverage': sorted(coverage), 'memory': memory.summary(), 'session': str(session), 'stats': stats()}


def control_check(controller, seed, reports):
    initial = controller.reset(seed)
    profile = get_profile(getattr(controller, 'profile', 'space_shooter'))
    if profile.id == 'ping_pong':
        if initial['tick'] != 0 or initial['score'] != {'player': 0, 'cpu': 0}: raise RuntimeError('Pong reset contract failed')
        frames, buttons, axis = 30, ['up'], 'y'
    else:
        if check_reset(initial): raise RuntimeError(f'Reset contract failed: {check_reset(initial)}')
        frames, buttons, axis = 120, ['right'], 'x'
    first = controller.step(frames, buttons)
    screenshot = controller.screenshot()
    second_initial = controller.reset(seed)
    second = controller.step(frames, buttons)
    if initial != second_initial or first != second:
        raise RuntimeError('Seeded replay produced different public states')
    if first['tick'] != initial['tick'] + frames or first['player'][axis] == initial['player'][axis]:
        raise RuntimeError('Frame stepping did not advance tick and move the player right')
    folder = Path(reports) / 'control-check'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'evidence.png').write_bytes(screenshot)
    (folder / 'states.json').write_text(json.dumps({'before': initial, 'after': first}, indent=2), encoding='utf-8')
    logging.info('Control check passed: movement and seeded replay match. Evidence: %s', folder)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description='SpaceQA exploratory game tester')
    parser.add_argument('--game', choices=['space_shooter', 'ping_pong'], help='Select a game and its configured URL')
    parser.add_argument('--control-check', action='store_true', help='Check browser integration without calling Gemini')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--watch', action='store_true', help='Animate actual game frames at about 60 FPS')
    parser.add_argument('--no-video', action='store_true', help='Disable original gameplay recording')
    parser.add_argument('--keep-open', action='store_true', help='Wait for Enter before closing the browser')
    parser.add_argument('--reports', default='reports')
    args = parser.parse_args()
    if args.headless and (args.watch or args.keep_open):
        parser.error('--watch and --keep-open require a visible browser')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    brain = None
    result = None
    started, started_clock = time.time(), time.monotonic()
    try:
        seed = int(os.getenv('GAME_SEED', '42'))
        steps = int(os.getenv('MAX_AGENT_STEPS', '20'))
        if steps < 1:
            raise ValueError('MAX_AGENT_STEPS must be positive')
        profile = get_profile(args.game or os.getenv('GAME_PROFILE', 'space_shooter'))
        url = (os.getenv(profile.id.upper() + '_URL') or profile.url) if args.game else os.getenv('GAME_URL', profile.url)
        if not args.control_check:
            key, model = os.getenv('GEMINI_API_KEY'), os.getenv('GEMINI_MODEL')
            if not key or not model:
                raise ValueError('Set GEMINI_API_KEY and GEMINI_MODEL in .env, or use --control-check')
            from .brain import Brain
            brain = Brain(key, model, rpm=int(os.getenv('GEMINI_RPM', '15')), profile=profile)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=args.headless)
            recording = None
            try:
                recording = RunRecording(browser, enabled=not args.no_video and not args.control_check)
                controller = GameController(recording.page, watch=args.watch or recording.enabled, profile=profile)
                if recording.enabled:
                    controller.recording_started = recording.started
                controller.connect(url)
                if args.control_check:
                    control_check(controller, seed, args.reports)
                else:
                    result = explore(controller, brain, seed=seed, max_steps=steps, game_url=url, reports=args.reports)
                    recording.report_folder = result.get('report')
            finally:
                if args.keep_open:
                    try:
                        input('Browser stays open. Press Enter here to close it... ')
                    except (EOFError, KeyboardInterrupt):
                        pass
                try:
                    if recording is not None:
                        recording.close()
                finally:
                    browser.close()
        if result is not None:
            summary_path = save_run_summary(args.reports, result, seed=seed, game_url=url, started=started,
                elapsed=time.monotonic()-started_clock, dashboard_id=os.getenv('SPACEQA_RUN_ID'))
            logging.info('Completed run stats: %s', summary_path)
    except Exception as exc:
        # Do not log SDK request details or credentials.
        if isinstance(exc, ValueError) or type(exc).__name__ == 'PlanningError':
            logging.error('%s', exc)
        else:
            logging.error('Run stopped (%s). Check the game API, URL and provider configuration.', type(exc).__name__)
        raise SystemExit(1) from None
    finally:
        if brain is not None:
            brain.close()


if __name__ == '__main__':
    main()
