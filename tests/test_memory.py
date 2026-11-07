import json
import tempfile
import unittest
from unittest.mock import Mock
from agent.memory import ExplorationMemory
from agent.models import NextExperiment
from agent.brain import Brain
from agent.main import explore
from test_agent import state, FakeGame


def plan(buttons=None, category='boundary'):
    return NextExperiment(frames=60, buttons=buttons or ['right'], hypothesis='Test interaction',
                          expected_behavior='Observe normal behavior', category=category)


class MemoryTests(unittest.TestCase):
    def test_memory_records_deltas_without_claiming_kills(self):
        memory = ExplorationMemory()
        before, after = state(), state(80, 60)
        before['enemies'] = [{'id': 'e1'}]
        after['score'] = 10
        after['player']['health'] = 2
        memory.record(before, after, plan(['right', 'shoot'], 'shooting'), 1)
        summary = memory.summary()
        self.assertEqual(summary['score_increase_events'], 1)
        self.assertEqual(summary['health_loss_events'], 1)
        self.assertEqual(summary['moving_shooting_batches'], 1)
        self.assertEqual(memory.recent[0]['enemy_ids_disappeared'], ['e1'])
        self.assertNotIn('kills', summary)

    def test_repeated_outward_movement_is_blocked_but_returning_and_firing_allowed(self):
        memory = ExplorationMemory()
        memory.record(state(), state(80, 60), plan(), 1)
        self.assertIsNotNone(memory.repetition_reason(plan(), state(80)))
        self.assertIsNone(memory.repetition_reason(plan(['left'], 'movement'), state(80)))
        self.assertIsNone(memory.repetition_reason(plan(['right', 'shoot'], 'shooting'), state(80)))

    def test_boundary_streak_requests_another_category(self):
        memory = ExplorationMemory()
        memory.record(state(), state(80, 60), plan(), 1)
        memory.record(state(80), state(0, 120), plan(['left']), 1)
        self.assertIsNotNone(memory.repetition_reason(plan(), state(0)))
        self.assertIsNone(memory.repetition_reason(plan(['shoot'], 'shooting'), state(0)))

    def test_brain_replans_and_receives_cumulative_memory(self):
        memory = ExplorationMemory()
        memory.record(state(), state(80, 60), plan(), 1)
        brain = Brain.__new__(Brain)
        brain.model = 'test-model'
        brain.pacer = Mock()
        brain.client = Mock()
        brain.client.models.generate_content.side_effect = [Mock(text=plan().model_dump_json()),
            Mock(text=plan(['left', 'shoot'], 'shooting').model_dump_json())]
        chosen = brain.choose_next(b'png', state(80), [], set(), memory=memory)
        self.assertEqual(chosen.buttons, ['left', 'shoot'])
        first, second = brain.client.models.generate_content.call_args_list
        context = json.loads(first.kwargs['contents'][1])
        self.assertEqual(context['exploration_memory']['edges_already_reached'], ['right'])
        self.assertIn('already reached', second.kwargs['contents'][1])

    def test_session_log_keeps_outcomes_across_game_resets(self):
        class EndingGame(FakeGame):
            def step(self, frames, buttons):
                after = super().step(frames, buttons)
                self.state['status'] = after['status'] = 'game_over'
                return after
        brain = Mock()
        brain.choose_next.return_value = plan(['shoot'], 'shooting')
        with tempfile.TemporaryDirectory() as root:
            result = explore(EndingGame(False), brain, seed=42, max_steps=3, game_url='game', reports=root)
            self.assertEqual(result['memory']['experiments'], 3)
            self.assertEqual(result['memory']['game_overs'], 3)
            with open(result['session']) as f:
                records = [json.loads(line) for line in f]
            self.assertEqual([r['outcome']['run'] for r in records], [1, 2, 3])
            self.assertEqual(records[-1]['memory']['shooting_batches'], 3)
