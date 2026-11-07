import unittest
from unittest.mock import Mock
from agent.brain import Brain


class BrainTests(unittest.TestCase):
    def test_invalid_response_retries_once(self):
        brain = Brain.__new__(Brain)
        brain.model = 'configured-model'
        brain.client = Mock()
        brain.client.models.generate_content.side_effect = [
            Mock(text='{"frames":999}'),
            Mock(text='{"frames":10,"buttons":["right"],"hypothesis":"Test edge","expected_behavior":"Stop at edge","category":"boundary"}')]
        plan = brain.choose_next(b'png', {}, [], set())
        self.assertEqual(plan.frames, 10)
        self.assertEqual(brain.client.models.generate_content.call_count, 2)

    def test_two_failures_stop_planning(self):
        brain = Brain.__new__(Brain)
        brain.model = 'configured-model'
        brain.client = Mock()
        brain.client.models.generate_content.side_effect = RuntimeError('provider unavailable')
        with self.assertRaises(RuntimeError):
            brain.choose_next(b'png', {}, [], set())
        self.assertEqual(brain.client.models.generate_content.call_count, 2)
