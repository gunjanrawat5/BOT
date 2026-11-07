import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from agent.brain import Brain, response_schema
from agent.models import BugAssessment, NextAction
from agent.memory import ExplorationMemory
from agent.main import explore
from test_agent import FakeGame, state, assessment


class AiDetectionTests(unittest.TestCase):
    def test_missing_score_report_without_invariant_violation(self):
        class CombatGame(FakeGame):
            def reset(self, seed):
                result = super().reset(seed)
                self.state['enemies'] = result['enemies'] = [{'id': 'enemy', 'x': 40, 'y': 30}]
                return result

            def step(self, frames, buttons):
                result = super().step(frames, buttons)
                self.state['enemies'] = result['enemies'] = []
                return result
        brain = Mock()
        brain.model = 'test-model'
        brain.choose_next.return_value = NextAction(frames=30, buttons=['shoot'],
            hypothesis='Test combat reward', expected_behavior='Score changes after a hit', category='shooting')
        brain.assess.return_value = assessment(True)
        game = CombatGame(False)
        with tempfile.TemporaryDirectory() as root:
            result = explore(game, brain, seed=42, max_steps=2, game_url='game', reports=root)
            folder = Path(result['report'])
            report = json.loads((folder / 'bug.json').read_text())
            self.assertEqual(report['before']['score'], report['after']['score'])
            self.assertEqual(report['detection_method'], 'gemini_assessment')
            self.assertEqual(report['status'], 'suspected')
            self.assertEqual(game.resets, 1)  # No hidden deterministic verification.
            self.assertTrue((folder / 'before.png').exists())
            self.assertIn('not independently verified', (folder / 'bug.md').read_text())

    def test_no_deterministic_rule_overrides_no_issue_judgment(self):
        brain = Mock()
        brain.model = 'test-model'
        brain.choose_next.return_value = NextAction(frames=100, buttons=['right'],
            hypothesis='Explore', expected_behavior='Move', category='movement')
        brain.assess.return_value = assessment(False)
        with tempfile.TemporaryDirectory() as root:
            result = explore(FakeGame(), brain, seed=42, max_steps=1, game_url='game', reports=root)
            self.assertNotIn('report', result)  # FakeGame moves out of bounds.
            self.assertEqual(result['steps'], 1)

    def test_assessor_sees_ordered_images_and_public_states(self):
        brain = Brain.__new__(Brain)
        brain.model = 'test-model'
        brain.pacer = Mock()
        brain.client = Mock()
        brain.client.models.generate_content.return_value = Mock(text=assessment(True).model_dump_json())
        plan = NextAction(frames=30, buttons=['shoot'], hypothesis='Combat', expected_behavior='Reward', category='shooting')
        result = brain.assess(before_screenshot=b'before', after_screenshot=b'after', before=state(), after=state(tick=30),
                              plan=plan, recent_history=[], memory=ExplorationMemory())
        self.assertTrue(result.issue_found)
        contents = brain.client.models.generate_content.call_args.kwargs['contents']
        self.assertEqual(contents[0].inline_data.data, b'before')
        self.assertEqual(contents[1].inline_data.data, b'after')
        self.assertEqual(json.loads(contents[2])['after']['tick'], 30)
        self.assertNotIn('additionalProperties', response_schema(BugAssessment))

    def test_failed_assessment_preserves_completed_action_log(self):
        brain = Mock()
        brain.model = 'test-model'
        brain.choose_next.return_value = NextAction(frames=30, buttons=['shoot'], hypothesis='Combat', expected_behavior='Reward', category='shooting')
        brain.assess.side_effect = RuntimeError('provider failed')
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(RuntimeError):
                explore(FakeGame(False), brain, seed=42, max_steps=1, game_url='game', reports=root)
            records = [json.loads(line) for line in next((Path(root) / 'sessions').glob('*.jsonl')).read_text().splitlines()]
            self.assertEqual(records[0]['event'], 'action')
            self.assertEqual(records[0]['outcome']['tick'], 30)
            self.assertFalse(list(Path(root).glob('*/bug.json')))
