"""The only boundary between the agent and the game."""
from .models import Action, GameState


class GameController:
    def __init__(self, page):
        self.page = page

    def connect(self, url: str):
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self.page.wait_for_function("""() => ['getState', 'reset', 'enableTestMode', 'stepFrames']
            .every(name => typeof window.gameTestAPI?.[name] === 'function')""", timeout=15000)
        self.page.evaluate("() => window.gameTestAPI.enableTestMode(true)")

    def get_state(self) -> dict:
        raw = self.page.evaluate("() => window.gameTestAPI.getState()")
        # Return only declared public fields, even if the game exposes extra internals.
        return GameState.model_validate(raw).model_dump()

    def reset(self, seed: int) -> dict:
        self.page.evaluate("seed => window.gameTestAPI.reset(seed)", seed)
        return self.get_state()

    def step(self, frames: int, buttons: list[str]) -> dict:
        action = Action(frames=frames, buttons=buttons)
        self.page.evaluate("arg => window.gameTestAPI.stepFrames(arg.frames, arg.buttons)", action.model_dump())
        return self.get_state()

    def screenshot(self) -> bytes:
        return self.page.locator("canvas").first.screenshot(type="png")
