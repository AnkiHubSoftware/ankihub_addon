import math
import re
from typing import Any, Dict, List, Optional, Tuple

import anki
import aqt
from anki.decks import DeckConfigDict, DeckConfigId, DeckId

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


def create_or_reset_deck_preset(
    preset_name=ANKIHUB_PRESET_NAME,
) -> DeckConfigDict:
    conf = next(
        (conf for conf in aqt.mw.col.decks.all_config() if conf["name"] == preset_name),
        None,
    )
    if conf:
        aqt.mw.col.decks.restore_to_default(conf)
    else:
        conf = aqt.mw.col.decks.add_config(preset_name)
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


def set_ankihub_config_for_deck(deck_id: DeckId, is_anking_deck: bool = False) -> None:
    from ..settings import config

    deck = aqt.mw.col.decks.get(deck_id, default=False)
    if not deck:
        return

    if is_anking_deck and config.get_feature_flags().get(
        "fsrs_in_recommended_deck_settings"
    ):
        conf = create_or_reset_deck_preset(preset_name="AnKing")
    else:
        conf = create_or_reset_deck_preset()

    deck["conf"] = conf["id"]
    aqt.mw.col.decks.update(deck)


def get_fsrs_version() -> Optional[int]:
    """Get the version of the FSRS scheduler available in the current Anki version."""
    deck_config_field_names = set(
        deck_config_pb2.DeckConfig.Config.DESCRIPTOR.fields_by_name.keys()
    )
    return max(
        (
            int(m.group(1))
            for field_name in deck_config_field_names
            if (m := re.search(r"fsrs_params_(\d)+", field_name))
        ),
        default=None,
    )


def get_fsrs_parameters(conf_id: DeckConfigId) -> Tuple[Optional[int], List[float]]:
    """Fetch the FSRS parameters for a deck config.
    Tries version = FSRS_VERSION down to the lowest FSRS version, returns the first found list or [].
    """
    from ..settings import FSRS_VERSION

    min_fsrs_version = 4  # The first version of FSRS that was used in Anki.
    deck_config = aqt.mw.col.decks.get_config(conf_id)
    for version in range(FSRS_VERSION, min_fsrs_version - 1, -1):
        params = deck_config.get(f"fsrsParams{version}", None)
        if params:
            return version, params

    return None, []


def fsrs_parameters_equal(parameters1: List[float], parameters2: List[float]) -> bool:
    """Check if two lists of FSRS parameters are close enough to be considered equal."""
    if len(parameters1) != len(parameters2):
        return False

    return all(
        # True if numbers differ by <= 6 units in the 5th decimal place
        math.isclose(param1, param2, abs_tol=6e-5, rel_tol=0.0)
        for param1, param2 in zip(parameters1, parameters2)
    )
