import json
import tempfile
import unittest
from unittest.mock import Mock
from pathlib import Path
from agent.controller import GameController
from agent.brain import Brain
from agent.memory import ExplorationMemory
from agent.models import NextAction
from agent.main import explore
from test_agent import FakeGame, assessment, state


def batch(events=None, cursor=0, truncated=False):
    return {'version':1,'events':events or [],'nextSequence':cursor,'truncated':truncated}


def event(sequence, kind='enemy_destroyed', tick=1):
    return {'sequence':sequence,'tick':tick,'type':kind,'data':{'enemy_id':'e1'}}


class EventTests(unittest.TestCase):
    def test_unsupported_api_is_not_an_empty_complete_timeline(self):
        page=Mock()
        page.evaluate.return_value=None
        controller=GameController(page)
        timeline=controller.read_events()
        self.assertFalse(timeline['available'])
        self.assertFalse(timeline['complete'])

    def test_incremental_cursor_and_truncation(self):
        page=Mock()
        controller=GameController(page)
        page.evaluate.return_value=batch([event(1)],1)
        self.assertTrue(controller.read_events()['complete'])
        page.evaluate.assert_called_with(unittest.mock.ANY,0)
        page.evaluate.return_value=batch([],1)
        self.assertEqual(controller.read_events()['events'],[])
        page.evaluate.assert_called_with(unittest.mock.ANY,1)
        page.evaluate.return_value=batch([event(9),event(10)],10,True)
        self.assertFalse(controller.read_events()['complete'])
        self.assertEqual(controller.event_cursor,10)

    def test_gaps_wrong_version_and_invalid_order_are_incomplete(self):
        for raw in [batch([event(2)],2),batch([event(2),event(1)],2),batch([],10**12),
                    dict(batch(),version=2),batch([event(1,tick=2),event(2,tick=1)],2)]:
            page=Mock()
            page.evaluate.return_value=raw
            controller=GameController(page)
            self.assertFalse(controller.read_events()['complete'])
            self.assertEqual(controller.event_cursor,0)

    def test_reset_restarts_event_cursor(self):
        page=Mock()
        controller=GameController(page)
        controller.event_cursor=100
        page.evaluate.side_effect=[None,batch([event(1,'game_reset',0)],1),state()]
        controller.reset(42)
        self.assertEqual(controller.event_cursor,1)
        self.assertEqual(page.evaluate.call_args_list[1].args[1],0)

    def test_assessor_gets_events_without_replacing_model_judgment(self):
        brain=Brain.__new__(Brain)
        brain.model='test'
        brain.pacer=Mock()
        brain.client=Mock()
        brain.client.models.generate_content.return_value=Mock(text=assessment().model_dump_json())
        timeline={'available':True,'complete':True,**batch([event(1)],1)}
        brain.assess(before_screenshot=b'before',after_screenshot=b'after',before=state(),after=state(tick=1),
            plan=NextAction(frames=1,buttons=['shoot'],hypothesis='Combat',expected_behavior='Reward',category='shooting'),
            recent_history=[],memory=ExplorationMemory(),timeline=timeline)
        content=brain.client.models.generate_content.call_args.kwargs['contents']
        self.assertEqual(json.loads(content[-1])['action_timeline'],timeline)

    def test_report_and_session_preserve_timeline(self):
        game=FakeGame(False)
        game.last_events={'available':True,'complete':True,**batch([event(1)],1)}
        brain=Mock()
        brain.model='test'
        brain.choose_next.return_value=NextAction(frames=1,buttons=['shoot'],hypothesis='Combat',expected_behavior='Reward',category='shooting')
        brain.assess.return_value=assessment(True)
        with tempfile.TemporaryDirectory() as root:
            result=explore(game,brain,seed=42,max_steps=1,game_url='game',reports=root)
            report=json.loads((Path(result['report'])/'bug.json').read_text())
            exported=json.loads((Path(result['report'])/'events.json').read_text())
            self.assertEqual(report['trace'][0]['timeline'],game.last_events)
            self.assertEqual(exported['actions'][0]['timeline'],game.last_events)
            first=json.loads(Path(result['session']).read_text().splitlines()[0])
            self.assertEqual(first['outcome']['timeline'],game.last_events)
