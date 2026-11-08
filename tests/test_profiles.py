import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from pydantic import ValidationError
from agent.models import Action, PongAction, NextPongAction
from agent.profiles import get_profile, execution_actions
from agent.brain import Brain
from agent.pong_guidance import interception
from agent.memory import ExplorationMemory
from agent.main import explore
from test_agent import assessment, state as shooter_state


def pong_state():
    return {'tick':0,'status':'ready','width':960,'height':480,
            'player':{'id':'player','x':32,'y':198,'width':12,'height':84},
            'cpu':{'id':'cpu','x':916,'y':198,'width':12,'height':84},
            'ball':{'x':480,'y':240,'radius':8,'vx':-5,'vy':1},
            'score':{'player':0,'cpu':0},'winningScore':7,'rally':0,'serveFrames':45,
            'rng':123,'seed':42,'events':[],'nextEventSequence':3}


class ProfileTests(unittest.TestCase):
    def test_wrong_profile_explains_how_to_select_the_actual_game(self):
        with self.assertRaisesRegex(ValueError, 'Game/profile mismatch.*space_shooter'):
            get_profile('ping_pong').normalize(shooter_state())
        with self.assertRaisesRegex(ValueError, 'Game/profile mismatch.*ping_pong'):
            get_profile('space_shooter').normalize(pong_state())

    def test_interception_predicts_wall_rebound_and_alignment_time(self):
        state=get_profile('ping_pong').normalize(pong_state())
        state.update(status='playing',serveFrames=0)
        state['ball'].update(x=102,y=450,vx=-5,vy=5)
        advice=interception(state,[{'plan':{'frames':10,'buttons':['down']},'movement_y':70}])
        self.assertEqual(advice['frames_until_contact'],10)
        self.assertEqual(advice['predicted_contact_y'],444)
        self.assertEqual(advice['observed_paddle_speed_per_frame'],7)
        self.assertEqual(advice['frames_to_align_estimate'],round(204/7,2))

    def test_interception_does_not_predict_through_serve_or_pause(self):
        state=get_profile('ping_pong').normalize(pong_state())
        self.assertNotIn('predicted_contact_y',interception(state,[]))
        state.update(status='paused',serveFrames=0)
        self.assertNotIn('predicted_contact_y',interception(state,[]))

    def test_pong_planning_window_is_short_but_replay_accepts_old_batches(self):
        with self.assertRaises(ValidationError):
            NextPongAction(frames=60,buttons=['up'],hypothesis='Intercept',expected_behavior='Return',category='rally')
        self.assertEqual(PongAction(frames=180,buttons=['up']).frames,180)

    def test_pong_state_is_public_and_keeps_both_scores(self):
        state=get_profile('ping_pong').normalize(pong_state())
        self.assertEqual(state['canvas'],{'width':960,'height':480})
        self.assertEqual(state['score'],{'player':0,'cpu':0})
        for key in ('rng','seed','events','nextEventSequence'):
            self.assertNotIn(key,state)
        self.assertNotIn('health',state['player'])

    def test_controls_are_game_specific(self):
        self.assertEqual(PongAction(frames=60,buttons=['up','pause']).buttons,['up','pause'])
        with self.assertRaises(ValidationError):Action(frames=1,buttons=['up'])
        with self.assertRaises(ValidationError):PongAction(frames=1,buttons=['shoot'])
        with self.assertRaises(ValidationError):PongAction(frames=1,buttons=['pause','pause'])

    def test_watch_export_pulses_pause_only_once(self):
        trace={'action':{'frames':3,'buttons':['down','pause']},'frame_by_frame':True,'pulse_buttons':['pause']}
        self.assertEqual(execution_actions(trace),[{'frames':1,'buttons':['down','pause']},{'frames':1,'buttons':['down']},{'frames':1,'buttons':['down']}])

    def test_pong_planner_gets_pong_schema_and_context(self):
        brain=Brain.__new__(Brain);brain.profile=get_profile('ping_pong');brain.model='test'
        brain.pacer=Mock();brain.client=Mock()
        plan=NextPongAction(frames=10,buttons=['up'],hypothesis='Intercept ball',expected_behavior='Return it',category='rally')
        brain.client.models.generate_content.return_value=Mock(text=plan.model_dump_json())
        state=brain.profile.normalize(pong_state())
        self.assertEqual(brain.choose_next(b'png',state,[],set(),ExplorationMemory('ping_pong')),plan)
        request=brain.client.models.generate_content.call_args.kwargs
        self.assertEqual(json.loads(request['contents'][-1])['game']['id'],'ping_pong')
        self.assertEqual(set(request['config'].response_schema['properties']['buttons']['items']['enum']),{'up','down','pause'})

    def test_pong_exploration_reports_and_stats_work_without_health(self):
        class FakePong:
            profile=get_profile('ping_pong');watch=True
            def reset(self,seed):self.state=self.profile.normalize(pong_state());return copy.deepcopy(self.state)
            def step(self,frames,buttons):
                self.state['tick']+=frames;self.state['status']='playing';self.state['player']['y']-=frames*7
                self.state['score']['player']=1
                return copy.deepcopy(self.state)
            def screenshot(self):return b'png'
        brain=Mock();brain.model='test-model'
        brain.choose_next.return_value=NextPongAction(frames=10,buttons=['up'],hypothesis='Rally',expected_behavior='Return ball',category='rally')
        brain.assess.return_value=assessment(True)
        with tempfile.TemporaryDirectory() as folder:
            result=explore(FakePong(),brain,seed=42,max_steps=2,game_url='game',reports=folder)
            self.assertEqual(result['stats']['final_score'],1)
            self.assertEqual(result['stats']['game_profile'],'ping_pong')
            report=json.loads((Path(result['report'])/'bug.json').read_text())
            self.assertEqual(report['game_profile'],'ping_pong')
            timeline=json.loads((Path(result['report'])/'replay_trace.json').read_text())
            self.assertEqual(timeline['game_profile'],'ping_pong')
            self.assertNotIn('health',report['after']['player'])

    def test_negative_score_is_observation_not_a_schema_verdict(self):
        raw=pong_state();raw['score']['player']=-1
        self.assertEqual(get_profile('ping_pong').normalize(raw)['score']['player'],-1)
