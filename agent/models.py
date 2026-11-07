"""Validated public telemetry and constrained model decisions."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


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


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    frames: int = Field(ge=1, le=180)
    buttons: list[Literal["left", "right", "shoot"]] = Field(default_factory=list, max_length=3)


class NextExperiment(Action):
    hypothesis: str = Field(min_length=1, max_length=1000)
    expected_behavior: str = Field(min_length=1, max_length=1000)
    category: Literal["movement", "boundary", "shooting", "collision", "survival", "exploration"]
