import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from agent.dashboard import Dashboard, make_handler


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'GEMINI_API_KEY': 'private-test-key', 'GEMINI_MODEL': 'test-model'})
        self.env.start()
        self.root = Path(self.temp.name)
        folder = self.root / 'reports' / 'sample'
        folder.mkdir(parents=True)
        (folder / 'bug.json').write_text(json.dumps({'title':'Missing score', 'status':'suspected', 'suspected_severity':'medium'}))
        (folder / 'before.png').write_bytes(b'png')
        self.app = Dashboard(self.root)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.app.stop()
        if self.app.process:
            self.app.process.wait(timeout=5)
        self.server.shutdown()
        self.server.server_close()
        self.env.stop()
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        merged = {'Content-Type':'application/json', 'X-SpaceQA-Token':self.app.token}
        merged.update(headers or {})
        conn.request(method,path,body=json.dumps(data).encode() if data is not None else None,headers=merged)
        response=conn.getresponse()
        body=response.read()
        conn.close()
        return response.status, body

    def test_reports_and_private_configuration(self):
        code, body = self.request('GET','/api/reports')
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body)[0]['title'],'Missing score')
        _, body = self.request('GET','/api/status')
        self.assertNotIn(b'private-test-key',body)
        self.assertTrue(json.loads(body)['settings']['configured'])
        code, body = self.request('GET','/')
        self.assertEqual(code,200)
        self.assertIn(self.app.token.encode(),body)
        self.assertNotIn(b'__SESSION_TOKEN__',body)

    def test_path_traversal_and_symlink_are_blocked(self):
        (self.root / 'secret.json').write_text('{"secret":true}')
        (self.root / 'reports' / 'escape.json').symlink_to(self.root / 'secret.json')
        for path in ['/artifacts/../secret.json','/artifacts/%2e%2e/secret.json','/artifacts/escape.json','/api/reports/%2e%2e']:
            self.assertEqual(self.request('GET',path)[0],404)

    def test_cross_origin_or_missing_token_cannot_start_runs(self):
        self.assertEqual(self.request('POST','/api/runs/start',{}, {'X-SpaceQA-Token':''})[0],403)
        self.assertEqual(self.request('POST','/api/runs/start',{}, {'Origin':'https://elsewhere.example'})[0],403)
        self.assertEqual(self.request('GET','/api/status',headers={'Host':'elsewhere.example'})[0],403)

    def test_invalid_parameters_do_not_launch_process(self):
        with patch('agent.dashboard.subprocess.Popen') as popen:
            for data in [{'max_steps':0},{'max_steps':True},{'game_url':'https://example.com'}, {'watch':'yes'}]:
                self.assertEqual(self.request('POST','/api/runs/start',data)[0],400)
            popen.assert_not_called()

    def test_start_stream_logs_reject_overlap_and_stop(self):
        real_popen = subprocess.Popen
        def dummy(command, **kwargs):
            return real_popen([sys.executable,'-u','-c',"import time; print('INFO Step 1, run 1: test private-test-key'); time.sleep(30)"],**kwargs)
        with patch('agent.dashboard.subprocess.Popen',side_effect=dummy):
            code, body = self.request('POST','/api/runs/start',{'max_steps':3,'seed':7,'watch':False})
            self.assertEqual(code,201)
            self.assertEqual(json.loads(body)['seed'],7)
            self.assertEqual(self.request('POST','/api/runs/start',{})[0],400)
            deadline=time.monotonic()+3
            while not self.app.logs and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertIn('[REDACTED]',self.app.logs[0])
            self.assertNotIn('private-test-key',self.app.logs[0])
            self.assertEqual(self.request('POST','/api/runs/stop',{})[0],200)
            deadline=time.monotonic()+3
            while self.app.current['status']=='stopping' and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual(self.app.current['status'],'stopped')
            metadata = next((self.root/'reports'/'dashboard-runs').glob('*/run.json'))
            self.assertEqual(json.loads(metadata.read_text())['status'],'stopped')

    def test_repair_launch_and_overlap_controls(self):
        game=self.root/'game';game.mkdir();(game/'package.json').write_text('{}')
        real_popen=subprocess.Popen
        def dummy(command,**kwargs):
            return real_popen([sys.executable,'-u','-c',"import time; print('INFO Repair: inspecting code (120s remaining)'); time.sleep(30)"],**kwargs)
        with patch.dict(os.environ,{'GAME_REPO':str(game)}), patch('agent.dashboard.subprocess.Popen',side_effect=dummy) as popen:
            self.assertEqual(self.request('POST','/api/repairs/start',{'report_id':'../escape'})[0],400)
            code,body=self.request('POST','/api/repairs/start',{'report_id':'sample'})
            self.assertEqual(code,201)
            self.assertEqual(json.loads(body)['kind'],'repair')
            self.assertIn('agent.repair',popen.call_args.args[0])
            self.assertEqual(self.request('POST','/api/runs/start',{})[0],400)
            self.assertEqual(self.request('POST','/api/repairs/start',{'report_id':'sample'})[0],400)
            self.assertEqual(self.request('POST','/api/repairs/apply',{'repair_id':'anything'})[0],400)
            self.assertEqual(self.request('POST','/api/runs/stop',{})[0],200)

    def test_repair_result_is_linked_without_rewriting_report(self):
        folder=self.root/'reports'/'repairs'/'candidate';folder.mkdir(parents=True)
        result={'report_id':'sample','status':'checks_passed','summary':'Fixed cause','changes':[]}
        (folder/'result.json').write_text(json.dumps(result))
        (folder/'fix.patch').write_text('diff --git a/src/game.js b/src/game.js')
        code,body=self.request('GET','/api/reports/sample')
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body)['repair']['status'],'checks_passed')
        self.assertEqual(self.request('GET','/artifacts/repairs/candidate/fix.patch')[0],200)
        original=json.loads((self.root/'reports'/'sample'/'bug.json').read_text())
        self.assertNotIn('repair',original)

    def test_video_route_supports_seek_ranges(self):
        payload=bytes(range(100))
        (self.root/'reports'/'sample'/'evidence.webm').write_bytes(payload)
        for value,expected,code in [('bytes=10-19',payload[10:20],206),('bytes=-8',payload[-8:],206),('bytes=95-',payload[95:],206),('bytes=100-',b'',416)]:
            connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
            connection.request('GET','/artifacts/sample/evidence.webm',headers={'Range':value})
            response=connection.getresponse()
            self.assertEqual(response.status,code)
            if code==206:
                self.assertEqual(response.getheader('Content-Type'),'video/webm')
                self.assertEqual(response.getheader('Accept-Ranges'),'bytes')
            self.assertEqual(response.read(),expected)
            connection.close()

    def test_completed_run_stats_endpoint(self):
        folder=self.root/'reports'/'dashboard-runs'/'completed';folder.mkdir(parents=True)
        (folder/'run.json').write_text(json.dumps({'id':'completed','status':'completed','started':10,'ended':35}))
        (folder/'summary.json').write_text(json.dumps({'status':'completed','actions':4,'final_score':0,'best_score':100}))
        code,body=self.request('GET','/api/stats')
        self.assertEqual(code,200)
        stats=json.loads(body)
        self.assertEqual(stats['totals']['completed'],1)
        self.assertEqual(stats['runs'][0]['duration_seconds'],25)
        self.assertEqual(stats['runs'][0]['final_score'],0)

    def test_profiles_route_old_and_pong_reports_to_their_own_repos(self):
        shooter=self.root/'shooter';pong=self.root/'pong'
        for repo in (shooter,pong):repo.mkdir();(repo/'package.json').write_text('{}')
        report=self.root/'reports'/'pong-finding';report.mkdir();(report/'bug.json').write_text('{"game_profile":"ping_pong"}')
        real_popen=subprocess.Popen
        def dummy(command,**kwargs):return real_popen([sys.executable,'-u','-c','import time;time.sleep(30)'],**kwargs)
        env={'GAME_PROFILE':'ping_pong','GAME_REPO':str(pong),'SPACE_SHOOTER_REPO':str(shooter),'PING_PONG_REPO':str(pong)}
        with patch.dict(os.environ,env),patch('agent.dashboard.subprocess.Popen',side_effect=dummy) as popen:
            self.assertEqual(self.app.game_repo(),pong.resolve())
            for report_id,expected in [('sample',shooter),('pong-finding',pong)]:
                code,_=self.request('POST','/api/repairs/start',{'report_id':report_id})
                self.assertEqual(code,201)
                args=popen.call_args.args[0]
                self.assertEqual(args[args.index('--repo')+1],str(expected.resolve()))
                self.app.stop();self.app.process.wait(timeout=5)
                deadline=time.monotonic()+3
                while self.app.current['status']=='stopping' and time.monotonic()<deadline:time.sleep(.01)
