"""Real Chromium against an in-page API fixture; no game repo required."""
import unittest
from playwright.sync_api import sync_playwright
from agent.controller import GameController


class BrowserTests(unittest.TestCase):
    def test_declared_contract_and_screenshot(self):
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content('<canvas width="100" height="100"></canvas>')
                page.evaluate('''() => {
                    let state;
                    window.gameTestAPI = {
                        enableTestMode: () => {},
                        reset: seed => { state = {
                            tick: 0, status: 'playing', canvas: {width:100,height:100},
                            player: {x:40,y:80,width:20,height:20,health:3,maxHealth:3,alive:true},
                            score: 0, enemies: [], projectiles: [], privateFlag: true
                        }; },
                        getState: () => state,
                        stepFrames: (frames, buttons) => {
                            state.tick += frames;
                            if (buttons.includes('right')) state.player.x = Math.min(80, state.player.x + frames);
                        }
                    };
                }''')
                controller = GameController(page)
                first = controller.reset(42)
                self.assertNotIn('privateFlag', first)
                after = controller.step(120, ['right'])
                self.assertEqual(after['player']['x'], 80)
                self.assertEqual(after['tick'], 120)
                self.assertTrue(controller.screenshot().startswith(b'\x89PNG'))
                controller.reset(42)
                self.assertEqual(controller.step(120, ['right']), after)
                watching = GameController(page, watch=True)
                watching.reset(42)
                self.assertEqual(watching.step(12, ['right']), dict(after, tick=12, player=dict(after['player'], x=52)))
                watching.reset(42)
                self.assertEqual(watching.step(12, ['right'])['player']['x'], 52)
            finally:
                browser.close()
