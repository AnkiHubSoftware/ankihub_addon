from typing import Any, Dict, Tuple

import anki
import aqt
from anki.decks import DeckConfigDict, DeckId

try:
    from anki import deck_config_pb2
except ImportError:
    from anki import deckconfig_pb2 as deck_config_pb2  # type: ignore

ANKIHUB_PRESET_NAME = "AnkiHub"
DECK_CONFIG: Dict[str, Any] = {
    "steps": [15, 1440],
    "fsrs_steps": [15],
    "new_order": anki.consts.NEW_CARDS_DUE,
    "new_gather_priority": deck_config_pb2.DeckConfig.Config.NewCardGatherPriority.NEW_CARD_GATHER_PRIORITY_LOWEST_POSITION,  # noqa: E501
    "new_sort_order": deck_config_pb2.DeckConfig.Config.NewCardSortOrder.NEW_CARD_SORT_ORDER_NO_SORT,
    "new_mix": deck_config_pb2.DeckConfig.Config.ReviewMix.REVIEW_MIX_AFTER_REVIEWS,
    "interday_learning_mix": deck_config_pb2.DeckConfig.Config.ReviewMix.REVIEW_MIX_BEFORE_REVIEWS,
    "review_order": deck_config_pb2.DeckConfig.Config.ReviewCardOrder.REVIEW_CARD_ORDER_DAY,
    "daily_limit": 9999,
    "bury": True,
    "new_intervals": [3, 4, 0],
    "easy_bonus": 1.5,
    "starting_ease": 2500,
    "max_interval": 1825,
    "leech_action": 1,
    "new_interval": 0.2,
    "leech_threshold": 4,
}
CONFIG_NAME_TO_DECK_OPTIONS_PATH: Dict[str, Tuple[str, ...]] = {
    "steps": (
        "new.delays",
        "lapse.delays",
    ),
    "fsrs_steps": (
        "new.delays",
        "lapse.delays",
    ),
    "new_order": ("new.order",),
    "new_gather_priority": ("newGatherPriority",),
    "new_sort_order": ("newSortOrder",),
    "new_mix": ("newMix",),
    "interday_learning_mix": ("interdayLearningMix",),
    "review_order": ("reviewOrder",),
    "daily_limit": (
        "rev.perDay",
        "new.perDay",
    ),
    "bury": ("rev.bury", "new.bury", "buryInterdayLearning"),
    "new_intervals": ("new.ints",),
    "easy_bonus": ("rev.ease4",),
    "starting_ease": ("new.initialFactor",),
    "max_interval": ("rev.maxIvl",),
    "leech_action": ("lapse.leechAction",),
    "new_interval": ("lapse.mult",),
    "leech_threshold": ("lapse.leechFails",),
}


def deep_set(data: Dict, path: str, value: Any) -> None:
    d = data
    for k in path.split(".")[:-1]:
        d = d[k]
    last_key = path.rsplit(".")[-1]
    d[last_key] = value


def _create_deck_preset_if_not_exists() -> DeckConfigDict:
    conf = next(
        (
            conf
            for conf in aqt.mw.col.decks.all_config()
            if conf["name"] == ANKIHUB_PRESET_NAME
        ),
        None,
    )
    if conf:
        aqt.mw.col.decks.restore_to_default(conf)
    else:
        conf = aqt.mw.col.decks.add_config(ANKIHUB_PRESET_NAME)
    fsrs_enabled = aqt.mw.col.get_config("fsrs")
    for option, value in DECK_CONFIG.items():
        if (fsrs_enabled and f"fsrs_{option}" in DECK_CONFIG) or (
            not fsrs_enabled and option.startswith("fsrs_")
        ):
            continue
        option_paths = CONFIG_NAME_TO_DECK_OPTIONS_PATH[option]
        for path in option_paths:
            deep_set(conf, path, value)
    aqt.mw.col.decks.update_config(conf)
    return conf


def set_ankihub_config_for_deck(deck_id: DeckId) -> None:
    deck = aqt.mw.col.decks.get(deck_id, default=False)
    if not deck:
        return
    conf = _create_deck_preset_if_not_exists()
    deck["conf"] = conf["id"]
    aqt.mw.col.decks.update(deck)
