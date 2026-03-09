from enum import Enum
from dataclasses import dataclass


class EventType(Enum):
    MER_NEW_POST = "mer_new_post"
    DART_FILING  = "dart"
    NEWS_ARTICLE = "news"
    MACRO_ALERT  = "macro_alert"


@dataclass
class AnalysisConfig:
    event_type:      EventType
    max_rules:       int
    include_macro:   bool
    output_format:   str   # "summary" | "full_analysis" | "alert"
    telegram_channel: str  # "tier1" | "tier2"


ANALYSIS_CONFIGS: dict[EventType, AnalysisConfig] = {
    EventType.MER_NEW_POST: AnalysisConfig(
        event_type=EventType.MER_NEW_POST,
        max_rules=0,
        include_macro=True,
        output_format="summary",
        telegram_channel="tier1",
    ),
    EventType.DART_FILING: AnalysisConfig(
        event_type=EventType.DART_FILING,
        max_rules=10,
        include_macro=True,
        output_format="full_analysis",
        telegram_channel="tier2",
    ),
    EventType.NEWS_ARTICLE: AnalysisConfig(
        event_type=EventType.NEWS_ARTICLE,
        max_rules=8,
        include_macro=True,
        output_format="full_analysis",
        telegram_channel="tier2",
    ),
    EventType.MACRO_ALERT: AnalysisConfig(
        event_type=EventType.MACRO_ALERT,
        max_rules=5,
        include_macro=True,
        output_format="alert",
        telegram_channel="tier2",
    ),
}
