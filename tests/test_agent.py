import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from pydantic import ValidationError
from agent.models import Action, GameState, NextAction, BugAssessment
from agent.detector import check, check_reset
from agent.replay import verify
from agent.main import explore, control_check


def state(x=40, tick=0):
    return {'tick': tick, 'status': 'playing', 'canvas': {'width': 100, 'height': 100},
            'player': {'x': x, 'y': 80, 'width': 20, 'height': 20, 'health': 3, 'maxHealth': 3, 'alive': True},
            'score': 0, 'enemies': [], 'projectiles': []}


def assessment(found=False):
    return BugAssessment(issue_found=found, title='Possible missing score' if found else 'No issue',
        severity='medium', confidence='medium', expected_behavior='Successful combat should award score',
        actual_behavior='Score unchanged after apparent hit', evidence=['Before score 0; after score 0'],
        reasoning='Possible missing reward; a hit is inferred, not confirmed',
        follow_up='Repeat with a shorter action and confirm a hit')


class FakeGame:
    def __init__(self, broken=True):
        self.broken = broken
        self.resets = 0

    def reset(self, seed):
        self.resets += 1
        self.state = state()
        return copy.deepcopy(self.state)

    def step(self, frames, buttons):
        self.state['tick'] += frames
        if 'right' in buttons:
            self.state['player']['x'] += frames
            if not self.broken:
                self.state['player']['x'] = min(80, self.state['player']['x'])
        return copy.deepcopy(self.state)

    def screenshot(self):
        return b'evidence'


class AgentTests(unittest.TestCase):
    def test_actions_reject_unsafe_values(self):
        for kwargs in ({'frames': 181}, {'frames': True}, {'frames': 1, 'buttons': ['eval']}, {'frames': 1, 'code': 'x'}):
            with self.assertRaises(ValidationError):
                Action(**kwargs)

    def test_telemetry_strips_private_flags(self):
        self.assertNotIn('defect', GameState.model_validate(dict(state(), defect=True)).model_dump())

    def test_rules_and_reset(self):
        self.assertEqual(check_reset(state()), [])
        after = state(x=81)
        after['score'] = -1
        after['player']['health'] = -1
        self.assertEqual(set(check(state(), after)), {'horizontal_boundary_violation', 'score_decreased', 'invalid_health_range'})
        after = state(tick=1)
        self.assertIn('reset_correctness_violation', check_reset(after))

    def test_replay_requires_same_violation(self):
        trace = [{'action': {'frames': 100, 'buttons': ['right']}}]
        result = verify(FakeGame(), 42, trace, ['horizontal_boundary_violation', 'invalid_health_range'])
        self.assertEqual(result['successes']['horizontal_boundary_violation'], 3)
        self.assertFalse(result['verified'])
        self.assertFalse(verify(FakeGame(), 42, trace, ['horizontal_boundary_violation'], attempts=1)['verified'])
        self.assertFalse(verify(FakeGame(False), 42, trace, ['horizontal_boundary_violation'])['verified'])

    def test_loop_writes_model_finding_without_deterministic_verification(self):
        brain = Mock()
        brain.model = 'test-model'
        brain.assess.return_value = assessment()
        brain.assess.return_value = assessment(True)
        brain.choose_next.return_value = NextAction(frames=100, buttons=['right'], hypothesis='Test boundary', expected_behavior='Stop at edge', category='boundary')
        with tempfile.TemporaryDirectory() as root:
            game = FakeGame()
            game.watch = True
            result = explore(game, brain, seed=42, max_steps=2, game_url='http://game', reports=root)
            report = json.loads((Path(result['report']) / 'bug.json').read_text())
            self.assertEqual(report['status'], 'suspected')
            self.assertFalse(report['verification']['verified'])
            self.assertEqual(report['verification']['attempts'], 0)
            self.assertEqual(report['after']['player']['x'], 140)
            self.assertEqual(len(report['trace']), 1)
            exported = json.loads((Path(result['report']) / 'replay_trace.json').read_text())
            self.assertEqual(exported['actions'], [{'frames': 1, 'buttons': ['right']}] * 100)
            self.assertEqual((Path(result['report']) / 'evidence.png').read_bytes(), b'evidence')

    def test_control_check(self):
        with tempfile.TemporaryDirectory() as root:
            control_check(FakeGame(False), 42, root)
            self.assertTrue((Path(root) / 'control-check' / 'states.json').exists())

    def test_game_over_starts_new_replay_trace(self):
        class EndingGame(FakeGame):
            def step(self, frames, buttons):
                result = super().step(frames, buttons)
                if self.resets == 1:
                    self.state['status'] = 'game_over'
                    result['status'] = 'game_over'
                return result
        brain = Mock()
        brain.model = 'test-model'
        brain.assess.return_value = assessment()
        brain.assess.side_effect = [assessment(), assessment(True)]
        brain.choose_next.side_effect = [
            NextAction(frames=1, buttons=[], hypothesis='Wait', expected_behavior='Survive', category='survival'),
            NextAction(frames=100, buttons=['right'], hypothesis='Edge', expected_behavior='Stop', category='boundary')]
        with tempfile.TemporaryDirectory() as root:
            result = explore(EndingGame(), brain, seed=42, max_steps=2, game_url='game', reports=root)
            report = json.loads((Path(result['report']) / 'bug.json').read_text())
            self.assertEqual(len(report['trace']), 1)
            self.assertEqual(report['trace'][0]['before']['tick'], 0)


if __name__ == '__main__':
    unittest.main()
