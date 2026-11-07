import json
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
                        page.get_by_text('INFO Step 1, run 1: Aim and shoot',exact=True).wait_for()
                        page.get_by_role('button',name='Stop current run').click()
                        expect(page.locator('#metric-status')).to_have_text('Stopped')
                    page.set_viewport_size({'width':390,'height':844})
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    page.screenshot(path='/tmp/spaceqa-dashboard-mobile.png',full_page=True)
                finally:
                    browser.close()
        finally:
            fixture.tearDown()
