import unittest
from unittest.mock import Mock, patch
from agent.brain import Brain, response_schema, PlanningError, RequestPacer
from google.genai import errors


class BrainTests(unittest.TestCase):
    def test_wire_schema_omits_unsupported_field_but_local_validation_stays_strict(self):
        from agent.models import NextExperiment
        from pydantic import ValidationError
        self.assertNotIn('additionalProperties', response_schema())
        self.assertEqual(NextExperiment.model_config['extra'], 'forbid')
        with self.assertRaises(ValidationError):
            NextExperiment.model_validate_json('{"frames":1,"buttons":[],"hypothesis":"Test","expected_behavior":"Normal","category":"boundary","code":"unsafe"}')

    def test_invalid_response_retries_once(self):
        brain = Brain.__new__(Brain)
        brain.model = 'configured-model'
        brain.pacer = Mock()
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
        brain.pacer = Mock()
        brain.client = Mock()
        brain.client.models.generate_content.side_effect = RuntimeError('provider unavailable')
        with self.assertRaises(RuntimeError):
            brain.choose_next(b'png', {}, [], set())
        self.assertEqual(brain.client.models.generate_content.call_count, 2)

    @patch('agent.brain.time.sleep')
    def test_service_failure_waits_then_retries(self, sleep):
        brain = Brain.__new__(Brain)
        brain.model = 'configured-model'
        brain.pacer = Mock()
        brain.client = Mock()
        brain.client.models.generate_content.side_effect = errors.ServerError(504, {'error': {'message': 'timeout'}})
        with self.assertRaisesRegex(PlanningError, 'remained unavailable or timed out'):
            brain.choose_next(b'png', {}, [], set())
        sleep.assert_called_once_with(2)
        self.assertEqual(brain.client.models.generate_content.call_count, 2)

    def test_bad_request_is_not_retried(self):
        brain = Brain.__new__(Brain)
        brain.model = 'configured-model'
        brain.pacer = Mock()
        brain.client = Mock()
        brain.client.models.generate_content.side_effect = errors.ClientError(400, {'error': {'message': 'bad schema'}})
        with self.assertRaisesRegex(PlanningError, 'HTTP 400'):
            brain.choose_next(b'png', {}, [], set())
        self.assertEqual(brain.client.models.generate_content.call_count, 1)

    @patch('agent.brain.time.sleep')
    def test_rate_limit_has_longer_wait_and_accurate_error(self, sleep):
        brain = Brain.__new__(Brain)
        brain.model = 'configured-model'
        brain.pacer = Mock()
        brain.client = Mock()
        brain.client.models.generate_content.side_effect = errors.ClientError(429, {'error': {'message': 'quota'}})
        with self.assertRaisesRegex(PlanningError, 'rate limit or quota exceeded'):
            brain.choose_next(b'png', {}, [], set())
        sleep.assert_called_once_with(30)
        self.assertEqual(brain.client.models.generate_content.call_count, 2)

    def test_duplicate_buttons_are_rejected(self):
        from agent.models import Action
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            Action(frames=60, buttons=['shoot', 'shoot', 'shoot'])
        self.assertEqual(Action(frames=60, buttons=['left', 'shoot']).buttons, ['left', 'shoot'])

    @patch('agent.brain.time.sleep')
    @patch('agent.brain.time.monotonic')
    def test_pacing_spaces_requests_and_skips_unnecessary_waits(self, clock, sleep):
        pacer = RequestPacer(15)
        clock.side_effect = [0, 1, 4.25, 10, 10]
        pacer.wait()
        sleep.assert_not_called()
        pacer.wait()
        sleep.assert_called_once_with(3.25)
        pacer.wait()
        self.assertEqual(sleep.call_count, 1)

    def test_invalid_rpm_is_rejected(self):
        for rpm in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                RequestPacer(rpm)
