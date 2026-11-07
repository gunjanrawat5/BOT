import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from playwright.sync_api import sync_playwright, expect
from agent.video import RunRecording, attach_video
from agent.controller import GameController
from agent.models import NextAction
from agent.main import explore
from test_agent import assessment
import test_dashboard


class VideoTests(unittest.TestCase):
    def test_attach_and_failed_recording_preserve_report(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            (folder/'bug.json').write_text('{"status":"suspected","verification":{"verified":false}}')
            (folder/'bug.md').write_text('# Finding\n\n## Screenshots\n')
            video=Mock()
            video.save_as.side_effect=lambda path:Path(path).write_bytes(b'webm-data')
            attach_video(folder,video)
            report=json.loads((folder/'bug.json').read_text())
            self.assertEqual(report['video']['status'],'ready')
            self.assertFalse(report['verification']['verified'])
            self.assertIn('not an independent verification',(folder/'bug.md').read_text())
            video.save_as.side_effect=RuntimeError('encoder failed')
            attach_video(folder,video)
            self.assertEqual(json.loads((folder/'bug.json').read_text())['video']['status'],'unavailable')
            self.assertFalse((folder/'evidence.webm').exists())

    def test_original_gameplay_records_and_plays_in_dashboard(self):
        fixture=test_dashboard.DashboardTests()
        fixture.setUp()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch()
                try:
                    recording=RunRecording(browser)
                    self.assertTrue(recording.enabled)
                    page=recording.page
                    page.set_content('<style>body{margin:0;background:#0b1016}canvas{display:block}</style><canvas width="800" height="600"></canvas>')
                    page.evaluate('''() => {
                        let state;
                        const canvas=document.querySelector('canvas'), ctx=canvas.getContext('2d');
                        function draw(){ctx.fillStyle='#0b1016';ctx.fillRect(0,0,800,600);ctx.fillStyle='#bce878';ctx.fillRect(state.player.x,state.player.y,40,40);}
                        window.gameTestAPI={
                            enableTestMode:()=>{},
                            reset:()=>{state={tick:0,status:'playing',canvas:{width:800,height:600},player:{x:380,y:500,width:40,height:40,health:3,maxHealth:3,alive:true},score:0,enemies:[],projectiles:[]};draw();},
                            getState:()=>state,
                            stepFrames:(frames,buttons)=>{for(let i=0;i<frames;i++){state.tick++;if(buttons.includes('right'))state.player.x=Math.min(760,state.player.x+3);}draw();}
                        };
                    }''')
                    controller=GameController(page,watch=True)
                    controller.recording_started=recording.started
                    brain=Mock();brain.model='test-model'
                    brain.choose_next.return_value=NextAction(frames=45,buttons=['right'],hypothesis='Explore',expected_behavior='Move right',category='movement')
                    brain.assess.return_value=assessment(True)
                    result=explore(controller,brain,seed=42,max_steps=1,game_url='fixture',reports=fixture.root/'reports')
                    recording.report_folder=result['report']
                    pending=json.loads((Path(result['report'])/'bug.json').read_text())
                    self.assertEqual(pending['video']['status'],'pending')
                    recording.close()
                    folder=Path(result['report'])
                    report=json.loads((folder/'bug.json').read_text())
                    self.assertEqual(report['video']['status'],'ready')
                    self.assertGreater((folder/'evidence.webm').stat().st_size,100)
                    self.assertIn('recording_seconds',report['trace'][0])
                    viewer=browser.new_page()
                    errors=[];viewer.on('pageerror',lambda error:errors.append(str(error)))
                    viewer.goto(f'http://127.0.0.1:{fixture.server.server_port}')
                    viewer.get_by_role('button',name='Possible missing score',exact=False).click()
                    video=viewer.locator('.video-evidence video')
                    expect(video).to_be_visible()
                    # Exercise the media route and Chromium decoder, not just the player markup.
                    expect(viewer.locator('.video-duration')).to_contain_text('s recording')
                    viewer.get_by_role('button',name='▶ Play recording').click()
                    viewer.wait_for_function('() => document.querySelector(".video-evidence video").readyState >= 2')
                    self.assertEqual(video.evaluate('node => node.error'),None)
                    self.assertGreater(video.evaluate('node => node.videoWidth'),0)
                    self.assertGreater(video.evaluate('node => node.duration'),0)
                    expect(viewer.locator('.screenshot-details')).not_to_have_attribute('open','')
                    viewer.get_by_role('button',name='Jump to flagged action ↗').click()
                    self.assertEqual(errors,[])
                    # Older reports still show their screenshot evidence directly.
                    viewer.get_by_role('button',name='Missing score',exact=False).last.click()
                    expect(viewer.locator('.video-evidence')).to_have_count(0)
                    expect(viewer.locator('.evidence-grid')).to_be_visible()
                    # A stale backend or missing recording must show the actual HTTP failure.
                    old_path=fixture.root/'reports'/'sample'/'bug.json'
                    old_report=json.loads(old_path.read_text())
                    old_report['video']={'status':'ready','file':'missing.webm'}
                    old_path.write_text(json.dumps(old_report))
                    viewer.get_by_role('button',name='Refresh ↻').click()
                    expect(viewer.locator('.video-error')).to_contain_text('HTTP 404')
                    expect(viewer.locator('.video-error')).to_contain_text('Restart')
                finally:
                    browser.close()
        finally:
            fixture.tearDown()
