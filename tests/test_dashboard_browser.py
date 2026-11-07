import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from playwright.sync_api import sync_playwright, expect
import test_dashboard


class DashboardBrowserTests(unittest.TestCase):
    def test_reports_run_controls_and_responsive_layout(self):
        fixture = test_dashboard.DashboardTests()
        fixture.setUp()
        game=fixture.root/'game';game.mkdir();(game/'package.json').write_text('{}')
        os.environ['GAME_REPO']=str(game)
        real_popen = subprocess.Popen
        def dummy(command, **kwargs):
            return real_popen([sys.executable,'-u','-c',"import time; print('INFO Step 1, run 1: Aim and shoot'); print('INFO Outcome: score +0'); time.sleep(30)"],**kwargs)
        try:
            folder = fixture.root/'reports'/'sample'
            (folder/'bug.json').write_text(json.dumps({'title':'Missing score after combat','status':'suspected',
                'suspected_severity':'medium','confidence':'medium','seed':42,
                'expected_behavior':'A defeated enemy should award score', 'actual_behavior':'Score remains unchanged',
                'assessment':{'evidence':['Score is 0 before and after'], 'reasoning':'An apparent hit needs review', 'follow_up':'Repeat the action'},
                'verification':{'reason':'AI judgment; no replay performed'}, 'trace':[{'action':{'frames':60,'buttons':['shoot']}}],
                'evidence':{'before':'before.png','after':'evidence.png'}}))
            with sync_playwright() as p:
                browser=p.chromium.launch()
                try:
                    page=browser.new_page(viewport={'width':1440,'height':1100})
                    page.goto(f'http://127.0.0.1:{fixture.server.server_port}')
                    page.get_by_role('button',name='Missing score after combat',exact=False).click()
                    page.get_by_role('heading',name='Missing score after combat').wait_for()
                    expect(page.locator('#launch-flight')).to_be_hidden()
                    expect(page.locator('#repair-motion')).to_be_hidden()
                    self.assertEqual(page.locator('.finding.selected').count(),1)
                    self.assertIn('AI SUSPECTED',page.locator('.detail-status').inner_text())
                    self.assertEqual(page.locator('.downloads a').count(),3)
                    # Use actual PNG evidence for the browser view.
                    png=page.screenshot()
                    (folder/'before.png').write_bytes(png)
                    (folder/'evidence.png').write_bytes(png)
                    page.reload()
                    page.get_by_role('button',name='Missing score after combat',exact=False).click()
                    page.get_by_role('heading',name='Missing score after combat').wait_for()
                    page.screenshot(path='/tmp/spaceqa-dashboard-desktop.png',full_page=True)
                    with patch('agent.dashboard.subprocess.Popen',side_effect=dummy):
                        page.locator('#max-steps').fill('3')
                        page.locator('#watch').uncheck()
                        page.get_by_role('button',name='Start exploration').click()
                        page.get_by_role('button',name='Stop current run').wait_for()
                        expect(page.locator('#launch-flight')).to_be_visible()
                        self.assertEqual(page.locator('.flying-rocket').evaluate('(node) => getComputedStyle(node).animationName'),'rocket-cross')
                        page.emulate_media(reduced_motion='reduce')
                        self.assertEqual(page.locator('.flying-rocket').evaluate('(node) => getComputedStyle(node).animationName'),'none')
                        page.emulate_media(reduced_motion='no-preference')
                        page.screenshot(path='/tmp/spaceqa-rocket-active.png',full_page=True)
                        page.reload()
                        expect(page.locator('#launch-flight')).to_be_visible()
                        page.get_by_role('button',name='Missing score after combat',exact=False).click()
                        page.get_by_role('heading',name='Missing score after combat').wait_for()
                        page.get_by_text('INFO Step 1, run 1: Aim and shoot',exact=True).wait_for()
                        page.get_by_role('button',name='Stop current run').click()
                        expect(page.locator('#metric-status')).to_have_text('Stopped')
                        expect(page.locator('#launch-flight')).to_be_hidden()
                    page.locator('#game-url').fill('https://example.com')
                    page.get_by_role('button',name='Start exploration').click()
                    expect(page.locator('#form-error')).to_contain_text('localhost')
                    expect(page.locator('#launch-flight')).to_be_hidden()
                    page.locator('#game-url').fill('http://localhost:5173')
                    page.route('**/api/repairs/start',lambda route:route.fulfill(status=400,content_type='application/json',body='{"error":"Repair could not start"}'))
                    page.get_by_role('button',name='Fix bug ↗').click()
                    expect(page.locator('.repair-box .form-error')).to_have_text('Repair could not start')
                    expect(page.locator('#repair-motion')).to_be_hidden()
                    page.unroute('**/api/repairs/start')
                    with patch('agent.dashboard.subprocess.Popen',side_effect=dummy):
                        page.get_by_role('button',name='Fix bug ↗').click()
                        expect(page.locator('#run-info')).to_contain_text('REPAIR')
                        expect(page.locator('#repair-motion')).to_be_visible()
                        expect(page.locator('#launch-flight')).to_be_hidden()
                        self.assertEqual(page.locator('.swinging-hammer').evaluate('(node) => getComputedStyle(node).animationName'),'hammer-tap')
                        page.emulate_media(reduced_motion='reduce')
                        self.assertEqual(page.locator('.swinging-hammer').evaluate('(node) => getComputedStyle(node).animationName'),'none')
                        page.emulate_media(reduced_motion='no-preference')
                        page.screenshot(path='/tmp/spaceqa-hammer-active.png',full_page=True)
                        page.get_by_role('button',name='Stop current run').click()
                        expect(page.locator('#metric-status')).to_have_text('Stopped')
                    expect(page.locator('#repair-motion')).to_be_hidden()
                    repair_folder=fixture.root/'reports'/'repairs'/'ui-candidate';repair_folder.mkdir(parents=True)
                    repair_result={'report_id':'sample','status':'checks_passed','summary':'Targeted fix validated',
                        'repo':str(game),'changes':[{'path':'src/game.js'}],'validation':'Regression test and build passed'}
                    (repair_folder/'result.json').write_text(json.dumps(repair_result))
                    (repair_folder/'fix.patch').write_text('diff --git a/src/game.js b/src/game.js')
                    page.get_by_role('button',name='Refresh ↻').click()
                    page.get_by_role('button',name='Apply fix to game repo').wait_for()
                    expect(page.get_by_role('link',name='Review patch ↗')).to_be_visible()
                    def apply_candidate(data):
                        repair_result['status']='applied'
                        (repair_folder/'result.json').write_text(json.dumps(repair_result))
                        return repair_result
                    with patch.object(fixture.app,'apply_fix',side_effect=apply_candidate):
                        page.get_by_role('button',name='Apply fix to game repo').click()
                        expect(page.locator('.repair-status')).to_have_text('APPLIED')
                    page.set_viewport_size({'width':390,'height':844})
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    page.screenshot(path='/tmp/spaceqa-dashboard-mobile.png',full_page=True)
                finally:
                    browser.close()
        finally:
            fixture.tearDown()
