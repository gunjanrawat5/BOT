"""Validated public telemetry and constrained model decisions."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, allow_inf_nan=False)


class Canvas(PublicModel):
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class Player(PublicModel):
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    health: float
    maxHealth: float = Field(gt=0)
    alive: bool


class GameState(PublicModel):
    tick: int = Field(ge=0)
    status: str
    canvas: Canvas
    player: Player
    score: float
    enemies: list[dict]
    projectiles: list[dict]
    enemyProjectiles: list[dict] = Field(default_factory=list)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    frames: int = Field(ge=1, le=180)
    buttons: list[Literal["left", "right", "shoot"]] = Field(default_factory=list, max_length=3)


    @field_validator('buttons')
    @classmethod
    def unique_buttons(cls, buttons):
        if len(buttons) != len(set(buttons)):
            raise ValueError('Buttons must be unique; each is held for the entire frame batch')
        return buttons


class NextAction(Action):
    hypothesis: str = Field(min_length=1, max_length=1000)
    expected_behavior: str = Field(min_length=1, max_length=1000)
    category: Literal["movement", "boundary", "shooting", "collision", "survival", "exploration"]


class BugAssessment(BaseModel):
    """A model judgment, never a deterministic verification result."""
    model_config = ConfigDict(extra='forbid', strict=True)
    issue_found: bool
    title: str = Field(min_length=1, max_length=300)
    severity: Literal['low', 'medium', 'high', 'critical']
    confidence: Literal['low', 'medium', 'high']
    expected_behavior: str = Field(min_length=1, max_length=2000)
    actual_behavior: str = Field(min_length=1, max_length=2000)
    evidence: list[str] = Field(min_length=1, max_length=10)
    reasoning: str = Field(min_length=1, max_length=3000)
    follow_up: str = Field(min_length=1, max_length=1000)


class GameEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    sequence: int = Field(ge=1)
    tick: int = Field(ge=0)
    type: str = Field(min_length=1, max_length=64)
    data: dict


class EventBatch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal[1]
    events: list[GameEvent] = Field(max_length=4096)
    nextSequence: int = Field(ge=0)
    truncated: bool


class Paddle(PublicModel):
    id: str
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class Ball(PublicModel):
    x: float
    y: float
    radius: float = Field(gt=0)
    vx: float
    vy: float


class PongScore(PublicModel):
    player: int
    cpu: int


class PongState(PublicModel):
    tick: int = Field(ge=0)
    status: str
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    player: Paddle
    cpu: Paddle
    ball: Ball
    score: PongScore
    winningScore: int = Field(gt=0)
    winner: str | None = None
    rally: int = Field(ge=0)
    serveFrames: int = Field(ge=0)


class PongAction(Action):
    buttons: list[Literal['up', 'down', 'pause']] = Field(default_factory=list, max_length=3)


class NextPongAction(PongAction):
    frames: int = Field(ge=1, le=12, description='Short control window; use 1–3 frames near the target or contact')
    hypothesis: str = Field(min_length=1, max_length=1000)
    expected_behavior: str = Field(min_length=1, max_length=1000)
    category: Literal['movement', 'boundary', 'collision', 'scoring', 'rally', 'pause', 'exploration']
