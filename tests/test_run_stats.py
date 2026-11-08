import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from agent.run_stats import completed_runs, save_run_summary
from agent.main import explore
from agent.models import NextAction
from test_agent import FakeGame, assessment


class RunStatsTests(unittest.TestCase):
    def test_gameplay_time_recovers_frames_without_model_waits(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'sessions').mkdir()
            session=root/'sessions'/'run.jsonl'
            session.write_text('\n'.join(json.dumps({'event':'action','outcome':outcome}) for outcome in [
                {'plan':{'frames':120,'buttons':[]},'status':'playing'},
                {'plan':{'frames':60,'buttons':['pause']},'status':'paused'},
                {'plan':{'frames':61,'buttons':['pause']},'status':'playing'}]))
            result={'session':str(session),'stats':{'actions':3}}
            save_run_summary(root,result,seed=1,game_url='game',started=0,elapsed=100)
            row=completed_runs(root)['runs'][0]
            self.assertEqual(row['gameplay_seconds'],3)
            self.assertEqual(row['duration_seconds'],100)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/'reports'
        self.root.mkdir()

    def tearDown(self):self.temp.cleanup()

    def run_file(self,name,metadata):
        folder=self.root/'dashboard-runs'/name;folder.mkdir(parents=True)
        (folder/'run.json').write_text(json.dumps(metadata))
        return folder

    def test_only_completed_qa_runs_and_zero_score_are_shown(self):
        folder=self.run_file('complete',{'kind':'exploration','status':'completed','started':100,'ended':112,'max_steps':20})
        (folder/'summary.json').write_text(json.dumps({'status':'completed','actions':3,'final_score':0,'best_score':200,'outcome':'finding_saved','report_id':'finding','duration_seconds':11.5}))
        self.run_file('active',{'status':'running'})
        self.run_file('stopped',{'status':'stopped'})
        self.run_file('failed',{'status':'failed'})
        self.run_file('repair',{'kind':'repair','status':'completed'})
        result=completed_runs(self.root)
        self.assertEqual(len(result['runs']),1)
        row=result['runs'][0]
        self.assertEqual(row['final_score'],0)
        self.assertEqual(row['best_score'],200)
        self.assertEqual(row['duration_seconds'],11.5)
        self.assertEqual(result['totals']['completed'],1)

    def test_legacy_recovers_observed_best_and_report_score_without_guessing(self):
        sessions=self.root/'sessions';sessions.mkdir()
        session=sessions/'old.jsonl'
        session.write_text(json.dumps({'event':'action','outcome':{'score_delta':100,'run':1},'memory':{'best_score':100}})+'\n'+json.dumps({'event':'action','outcome':{'score_delta':0,'run':2},'memory':{'best_score':100}})+'\n')
        folder=self.run_file('old',{'status':'completed','started':10,'ended':30})
        (folder/'activity.log').write_text(f'INFO Session observations: {session}\nINFO Finished 2 actions; categories: []. Gemini flagged no suspected bugs.\n')
        row=completed_runs(self.root)['runs'][0]
        self.assertEqual(row['best_score'],100)
        self.assertIsNone(row['final_score'])
        self.assertEqual(row['actions'],2)
        self.assertEqual(row['outcome'],'no_findings')
        report=self.root/'finding';report.mkdir();(report/'bug.json').write_text('{"after":{"score":0}}')
        with (folder/'activity.log').open('a') as log:log.write(f'INFO AI suspected bug report: {report} (not independently verified)\n')
        row=completed_runs(self.root)['runs'][0]
        self.assertEqual(row['final_score'],0)
        self.assertEqual(row['report_id'],'finding')

    def test_new_summary_persists_final_score_and_cli_history(self):
        result={'session':'session.jsonl','stats':{'actions':5,'final_score':300,'best_score':500,'max_steps':20}}
        path=save_run_summary(self.root,result,seed=42,game_url='http://localhost:5173',started=100,elapsed=8.25)
        self.assertTrue(path.exists())
        row=completed_runs(self.root)['runs'][0]
        self.assertEqual(row['final_score'],300)
        self.assertEqual(row['duration_seconds'],8.25)
        self.assertEqual(row['best_score'],500)
        # Dashboard jobs use one summary, not a second CLI entry.
        folder=self.run_file('dashboard',{'status':'completed'})
        save_run_summary(self.root,result,seed=7,game_url='game',started=110,elapsed=10,dashboard_id='dashboard')
        self.assertTrue((folder/'summary.json').exists())
        self.assertEqual(completed_runs(self.root)['totals']['completed'],2)

    def test_bad_metadata_and_external_session_paths_do_not_break_stats(self):
        folder=self.run_file('legacy',{'status':'completed','started':None,'ended':None})
        (folder/'activity.log').write_text('INFO Session observations: /etc/anything.jsonl\n')
        bad=self.root/'dashboard-runs'/'bad';bad.mkdir();(bad/'run.json').write_text('broken json')
        row=completed_runs(self.root)['runs'][0]
        self.assertIsNone(row['final_score'])
        self.assertIsNone(row['duration_seconds'])

    def test_early_finding_reports_actual_action_count_and_score(self):
        brain=Mock();brain.model='test-model'
        brain.choose_next.return_value=NextAction(frames=30,buttons=['shoot'],hypothesis='Combat',expected_behavior='Reward',category='shooting')
        brain.assess.return_value=assessment(True)
        result=explore(FakeGame(False),brain,seed=42,max_steps=20,game_url='game',reports=self.root)
        self.assertEqual(result['stats']['actions'],1)
        self.assertEqual(result['stats']['gameplay_seconds'],0.5)
        self.assertEqual(result['stats']['max_steps'],20)
        self.assertEqual(result['stats']['final_score'],0)

    def test_main_saves_score_after_recording_finalization(self):
        from agent.main import main
        class ScoringGame(FakeGame):
            def step(self,frames,buttons):
                super().step(frames,buttons)
                self.state['score']+=50
                return dict(self.state)
        game=ScoringGame(False);game.connect=Mock()
        brain=Mock();brain.model='test-model'
        brain.choose_next.return_value=NextAction(frames=1,buttons=['shoot'],hypothesis='Score',expected_behavior='Reward',category='shooting')
        brain.assess.return_value=assessment()
        recording=Mock();recording.enabled=False
        target=self.root/'dashboard-runs'/'testdash'/'summary.json'
        recording.close.side_effect=lambda:self.assertFalse(target.exists())
        env={'GEMINI_API_KEY':'test-key','GEMINI_MODEL':'test-model','MAX_AGENT_STEPS':'1','GAME_SEED':'42','GAME_URL':'game','SPACEQA_RUN_ID':'testdash'}
        with patch.dict('os.environ',env), patch('sys.argv',['spaceqa','--headless','--no-video','--reports',str(self.root)]), patch('agent.brain.Brain',return_value=brain), patch('agent.main.sync_playwright'), patch('agent.main.RunRecording',return_value=recording), patch('agent.main.GameController',return_value=game):
            main()
        summary=json.loads(target.read_text())
        self.assertEqual(summary['status'],'completed')
        self.assertEqual(summary['final_score'],50)
        self.assertEqual(summary['best_score'],50)
        self.assertEqual(summary['actions'],1)
        recording.close.assert_called_once()
