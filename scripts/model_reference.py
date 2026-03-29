from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class ModelReference:
    canonical_name: str
    aliases: tuple[str, ...]
    description: str
    capabilities: tuple[str, ...]
    image_filename: str


MODEL_REFERENCES: tuple[ModelReference, ...] = (
    ModelReference(
        canonical_name="Ray-Ban Meta (Gen 2 - Current Model)",
        aliases=(
            "ray-ban meta",
            "ray ban meta",
            "rayban meta",
            "supernova",
            "supernova device media source",
            "supernova device",
            "ray ban meta smart glasses",
            "ray-ban meta smart glasses",
            "rayban meta smart glasses",
            "meta smart glasses",
            "meta wayfarer",
            "meta headliner",
            "meta skyler",
            "wayfarer",
            "headliner",
            "skyler",
            "rw4006",
            "rw4009",
            "rw4010",
        ),
        description="Best all-around option combining camera quality, AI features, and everyday usability.",
        capabilities=(
            "Upgraded video recording (up to about 3K resolution)",
            "Improved speakers and microphone clarity",
            "Enhanced Meta AI with better voice interaction and contextual assistance",
            "Extended battery life for longer daily use",
            "Multiple frame styles including Wayfarer, Headliner, and Skyler",
            "Faster performance and smoother usability",
        ),
        image_filename="rayban_meta_gen2.png",
    ),
    ModelReference(
        canonical_name="Ray-Ban Meta (Gen 1)",
        aliases=(
            "ray-ban meta gen 1",
            "ray ban meta gen 1",
            "rayban meta gen 1",
            "meta gen 1",
            "ray-ban stories",
            "ray ban stories",
            "rayban stories",
            "gen 1",
            "stories",
        ),
        description="Entry-level smart glasses focused on hands-free content capture, audio, and social media integration.",
        capabilities=(
            "12 MP camera for photos and 1080p video recording",
            "Open-ear speakers for music, calls, and notifications",
            "Built-in microphones with voice command support",
            "Basic Meta AI assistant integration",
            "Livestreaming to Instagram and Facebook",
            "Touch controls on the frame",
        ),
        image_filename="rayban_meta_gen1.png",
    ),
    ModelReference(
        canonical_name="Meta Ray-Ban Display (AR Glasses)",
        aliases=(
            "display",
            "hud",
            "hypernova",
            "hypernova display",
            "heads-up display",
            "heads up display",
            "ar glasses",
            "meta ray-ban display",
            "meta ray ban display",
            "meta rayban display",
        ),
        description="Advanced AR glasses offering immersive, real-time information and deeper AI interaction.",
        capabilities=(
            "Built-in heads-up display inside the lens",
            "AR features such as navigation, notifications, and live translation",
            "See-what-you-see AI capabilities for real-world context",
            "Gesture control support via a neural wristband",
            "Camera and audio capabilities similar to Gen 2",
        ),
        image_filename="meta_rayban_display.png",
    ),
    ModelReference(
        canonical_name="Oakley Meta (Sport-Focused Models)",
        aliases=(
            "oakley meta",
            "oakley",
            "sport",
            "sphaera",
            "athlete",
        ),
        description="Performance-focused smart glasses built for athletes, outdoor use, and activity tracking.",
        capabilities=(
            "Sport-oriented design for durability and outdoor use",
            "Built-in camera for recording activities",
            "Longer battery life for extended sessions",
            "Integration with fitness platforms such as Strava and Garmin",
            "Interchangeable lenses for different environments",
        ),
        image_filename="oakley_meta.png",
    ),
)


def get_model_assets_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "models"


def normalize_model_text(value: str) -> str:
    text = str(value or "").lower()
    text = text.replace("_", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
