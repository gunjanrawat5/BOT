"""The only boundary between the agent and the game."""
import logging
from .models import Action, GameState, EventBatch


class GameController:
    def __init__(self, page, *, watch=False):
        self.page = page
        self.watch = watch
        self.event_cursor = 0
        self.last_events = {'available': False, 'complete': False, 'events': [], 'reason': 'Event API not read yet'}

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
        self.event_cursor = 0
        self.last_events = self.read_events()
        logging.info('Game event timeline: %s', 'available' if self.last_events['available'] else 'unavailable (getEvents not provided)')
        return self.get_state()

    def step(self, frames: int, buttons: list[str]) -> dict:
        action = Action(frames=frames, buttons=buttons)
        baseline = self.read_events()
        if self.watch:
            # Advance only via the declared API; yield between frames so the
            # browser paints actual intermediate game states. Replay uses the
            # same controller and therefore the same single-frame calls.
            self.page.evaluate("""async arg => {
                for (let frame = 0; frame < arg.frames; frame++) {
                    window.gameTestAPI.stepFrames(1, arg.buttons);
                    await new Promise(resolve => setTimeout(resolve, 1000 / 60));
                }
            }""", action.model_dump())
        else:
            self.page.evaluate("arg => window.gameTestAPI.stepFrames(arg.frames, arg.buttons)", action.model_dump())
        self.last_events = self.read_events()
        if baseline['available'] and not baseline['complete']:
            self.last_events.update(complete=False, reason='Pre-action event cursor was incomplete or invalid')
        return self.get_state()

    def read_events(self):
        cursor = self.event_cursor
        try:
            raw = self.page.evaluate("""cursor => typeof window.gameTestAPI.getEvents === 'function'
                ? window.gameTestAPI.getEvents(cursor) : null""", cursor)
            if raw is None:
                return {'available': False, 'complete': False, 'events': [],
                        'reason': 'Game does not implement getEvents; intermediate events are unknown'}
            batch = EventBatch.model_validate(raw)
            sequences = [event.sequence for event in batch.events]
            if batch.nextSequence < cursor or sequences != sorted(set(sequences)):
                raise ValueError('Non-monotonic event sequence')
            if any(seq <= cursor or seq > batch.nextSequence for seq in sequences):
                raise ValueError('Event outside requested cursor range')
            if sequences and sequences[-1] != batch.nextSequence:
                raise ValueError('Response omits latest event')
            if not batch.truncated and (len(sequences) != batch.nextSequence - cursor or any(seq != cursor + i + 1 for i, seq in enumerate(sequences))):
                raise ValueError('Missing events without truncation marker')
            if any(b != a + 1 for a, b in zip(sequences, sequences[1:])):
                raise ValueError('Gap inside retained events')
            if batch.truncated and not sequences and batch.nextSequence > cursor:
                raise ValueError('Truncated response has no retained events')
            if any(a.tick > b.tick for a, b in zip(batch.events, batch.events[1:])):
                raise ValueError('Events are not in tick order')
            self.event_cursor = batch.nextSequence
            return {'available': True, 'complete': not batch.truncated, **batch.model_dump(),
                    'reason': 'Some requested events were evicted' if batch.truncated else 'Complete action timeline'}
        except Exception as exc:
            logging.warning('Game event timeline could not be validated (%s)', type(exc).__name__)
            return {'available': True, 'complete': False, 'events': [],
                    'reason': 'Invalid event telemetry or event API failure; absence of events proves nothing'}

    def screenshot(self) -> bytes:
        return self.page.locator("canvas").first.screenshot(type="png")
