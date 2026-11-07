"""CLI for browser control checks and autonomous exploration."""
import argparse
import json
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from .memory import ExplorationMemory
from .controller import GameController
from .detector import check, check_reset
from .replay import verify
from .reports import write_report


def explore(controller, brain, *, seed, max_steps, game_url, reports):
    history, trace, coverage = [], [], set()
    memory = ExplorationMemory()
    session = Path(reports) / 'sessions' / f'{datetime.now(timezone.utc):%Y%m%dT%H%M%S_%fZ}_{uuid4().hex[:8]}.jsonl'
    session.parent.mkdir(parents=True, exist_ok=True)
    logging.info('Session observations: %s', session)
    run = 1
    state = controller.reset(seed)
    for step in range(max_steps):
        violations = check_reset(state) if not trace else []
        before = state
        if not violations:
            if state['status'] == 'game_over':
                run += 1
                trace = []
                state = controller.reset(seed)
                before = state
                violations = check_reset(state)
            if not violations:
                plan = brain.choose_next(controller.screenshot(), state, history[-5:], coverage, memory=memory)
                logging.info('Step %d, run %d: %s (%s, %d frames)', step + 1, run, plan.hypothesis, plan.buttons, plan.frames)
                action = {'frames': plan.frames, 'buttons': plan.buttons}
                state = controller.step(**action)
                trace.append({'action': action, 'frame_by_frame': getattr(controller, 'watch', False), 'plan': plan.model_dump(), 'before': before, 'after': state})
                outcome = memory.record(before, state, plan, run)
                history.append(outcome)
                with session.open('a', encoding='utf-8') as log:
                    log.write(json.dumps({'step': step + 1, 'outcome': outcome, 'memory': memory.summary()}) + '\n')
                logging.info('Outcome: x %g → %g; score %+g; health %+g; enemies %d → %d; status %s',
                             before['player']['x'], state['player']['x'], outcome['score_delta'],
                             outcome['health_delta'], len(before['enemies']), len(state['enemies']), state['status'])
                coverage.add(plan.category)
                violations = check(before, state)
        if violations:
            evidence = controller.screenshot()
            verification = verify(controller, seed, trace, violations)
            folder = write_report(reports, seed=seed, game_url=game_url, trace=trace,
                violations=violations, before=before, after=state, screenshot=evidence, verification=verification)
            logging.info('Issue %s; report: %s', 'verified' if verification['verified'] else 'unverified', folder)
            return {'report': str(folder), 'verification': verification}
    logging.info('Finished %d experiments; categories: %s. No checked invariant violated.', max_steps, sorted(coverage))
    return {'steps': max_steps, 'coverage': sorted(coverage), 'memory': memory.summary(), 'session': str(session)}


def control_check(controller, seed, reports):
    initial = controller.reset(seed)
    if check_reset(initial):
        raise RuntimeError(f'Reset contract failed: {check_reset(initial)}')
    first = controller.step(120, ['right'])
    screenshot = controller.screenshot()
    second_initial = controller.reset(seed)
    second = controller.step(120, ['right'])
    if initial != second_initial or first != second:
        raise RuntimeError('Seeded replay produced different public states')
    if first['tick'] != initial['tick'] + 120 or first['player']['x'] == initial['player']['x']:
        raise RuntimeError('Frame stepping did not advance tick and move the player right')
    folder = Path(reports) / 'control-check'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'evidence.png').write_bytes(screenshot)
    (folder / 'states.json').write_text(json.dumps({'before': initial, 'after': first}, indent=2), encoding='utf-8')
    logging.info('Control check passed: movement and seeded replay match. Evidence: %s', folder)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description='SpaceQA exploratory game tester')
    parser.add_argument('--control-check', action='store_true', help='Check browser integration without calling Gemini')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--watch', action='store_true', help='Animate actual game frames at about 60 FPS')
    parser.add_argument('--keep-open', action='store_true', help='Wait for Enter before closing the browser')
    parser.add_argument('--reports', default='reports')
    args = parser.parse_args()
    if args.headless and (args.watch or args.keep_open):
        parser.error('--watch and --keep-open require a visible browser')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    brain = None
    try:
        seed = int(os.getenv('GAME_SEED', '42'))
        steps = int(os.getenv('MAX_AGENT_STEPS', '20'))
        if steps < 1:
            raise ValueError('MAX_AGENT_STEPS must be positive')
        url = os.getenv('GAME_URL', 'http://localhost:5173')
        if not args.control_check:
            key, model = os.getenv('GEMINI_API_KEY'), os.getenv('GEMINI_MODEL')
            if not key or not model:
                raise ValueError('Set GEMINI_API_KEY and GEMINI_MODEL in .env, or use --control-check')
            from .brain import Brain
            brain = Brain(key, model, rpm=int(os.getenv('GEMINI_RPM', '15')))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=args.headless)
            try:
                controller = GameController(browser.new_page(), watch=args.watch)
                controller.connect(url)
                if args.control_check:
                    control_check(controller, seed, args.reports)
                else:
                    explore(controller, brain, seed=seed, max_steps=steps, game_url=url, reports=args.reports)
            finally:
                if args.keep_open:
                    try:
                        input('Browser stays open. Press Enter here to close it... ')
                    except (EOFError, KeyboardInterrupt):
                        pass
                browser.close()
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
