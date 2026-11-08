"""Explicit game contracts; no implicit guesses from screenshots or ports."""
from dataclasses import dataclass
from pydantic import ValidationError
from .models import Action, NextAction, GameState, PongAction, NextPongAction, PongState


@dataclass(frozen=True)
class GameProfile:
    id: str
    name: str
    url: str
    repo: str
    buttons: tuple
    pulse_buttons: tuple
    action_model: type
    plan_model: type
    state_model: type

    def normalize(self, raw):
        try:
            state = self.state_model.model_validate(raw).model_dump()
        except ValidationError:
            for other in PROFILES.values():
                if other.id == self.id: continue
                try:
                    other.state_model.model_validate(raw)
                except ValidationError:
                    continue
                raise ValueError(f'Game/profile mismatch: selected {self.name}, but the game exposes {other.name} telemetry. '
                                 f'Select {other.name} in the dashboard, or run uv run spaceqa --game {other.id}. '
                                 'Check that the Game URL points to the selected game.') from None
            raise
        if self.id == 'ping_pong':
            state['canvas'] = {'width': state.pop('width'), 'height': state.pop('height')}
        return state

    def describe(self):
        return {'id': self.id, 'name': self.name, 'buttons': list(self.buttons),
                'once_per_batch_buttons': list(self.pulse_buttons),
                'score_meaning': 'Player points; CPU points are separate' if self.id == 'ping_pong' else 'Player score'}


PROFILES = {
    'space_shooter': GameProfile('space_shooter', 'Space Shooter', 'http://localhost:5173', '../SpaceShooter',
        ('left', 'right', 'shoot'), (), Action, NextAction, GameState),
    'ping_pong': GameProfile('ping_pong', 'Ping Pong', 'http://localhost:5174', '../PingPong',
        ('up', 'down', 'pause'), ('pause',), PongAction, NextPongAction, PongState),
}


def get_profile(value='space_shooter'):
    if isinstance(value, GameProfile): return value
    if not isinstance(value, str) or value not in PROFILES:
        raise ValueError('Unknown game profile; use space_shooter or ping_pong')
    return PROFILES[value]


def player_score(state):
    score = state['score']
    return score['player'] if isinstance(score, dict) else score


def execution_actions(record):
    """Export the actual call sequence, including one-shot pause semantics."""
    action = record['action']
    if not record.get('frame_by_frame'):
        return [action]
    pulse = set(record.get('pulse_buttons', []))
    return [{'frames': 1, 'buttons': list(action['buttons']) if i == 0 else [b for b in action['buttons'] if b not in pulse]}
            for i in range(action['frames'])]
