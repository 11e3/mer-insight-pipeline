from enum import Enum


class EventType(Enum):
    MER_NEW_POST = "mer_new_post"
    DART_FILING  = "dart"
    NEWS_ARTICLE = "news"
    MACRO_ALERT  = "macro_alert"
