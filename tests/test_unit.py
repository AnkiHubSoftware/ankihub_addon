from pytest_anki import AnkiSession


def test_lowest_level_common_ancestor_deck_name(anki_session_with_addon: AnkiSession):
    from ankihub.utils import lowest_level_common_ancestor_deck_name

    deck_names = [
        "A",
        "A::B",
    ]
    assert lowest_level_common_ancestor_deck_name(deck_names) == "A"

    deck_names = [
        "A::B::C",
        "A::B::C::D",
        "A::B",
    ]
    assert lowest_level_common_ancestor_deck_name(deck_names) == "A::B"

    deck_names = ["A::B::C", "A::B::C::D", "A::B", "B"]
    assert lowest_level_common_ancestor_deck_name(deck_names) is None


def test_updated_tags(anki_session_with_addon: AnkiSession):
    from ankihub.sync import ADDON_INTERNAL_TAGS, updated_tags

    assert set(
        updated_tags(
            cur_tags=[],
            incoming_tags=["A", "B"],
            protected_tags=[],
        )
    ) == set(["A", "B"])

    # dont delete protected tags
    assert set(
        updated_tags(
            cur_tags=["A", "B"],
            incoming_tags=[],
            protected_tags=["A"],
        )
    ) == set(["A"])

    # dont delete tags that contain protected tags
    assert set(
        updated_tags(
            cur_tags=["A::B::C"],
            incoming_tags=[],
            protected_tags=["A"],
        )
    ) == set(["A::B::C"])

    assert set(
        updated_tags(
            cur_tags=["A::B::C"],
            incoming_tags=[],
            protected_tags=["B"],
        )
    ) == set(["A::B::C"])

    assert set(
        updated_tags(
            cur_tags=["A::B::C"],
            incoming_tags=[],
            protected_tags=["C"],
        )
    ) == set(["A::B::C"])

    # keep add-on internal tags
    assert set(
        updated_tags(
            cur_tags=ADDON_INTERNAL_TAGS,
            incoming_tags=[],
            protected_tags=[],
        )
    ) == set(ADDON_INTERNAL_TAGS)

    # keep Anki internal tags
    assert set(
        updated_tags(
            cur_tags=["marked", "leech"],
            incoming_tags=[],
            protected_tags=[],
        )
    ) == set(["marked", "leech"])


def test_normalize_url(anki_session_with_addon: AnkiSession):
    from ankihub.error_reporting import normalize_url

    url = "https://app.ankihub.net/api/decks/fc39e7e7-9705-4102-a6ec-90d128c64ed3/updates?since=2022-08-01T1?6%3A32%3A2"
    assert normalize_url(url) == "https://app.ankihub.net/api/decks/<id>/updates"

    url = "https://app.ankihub.net/api/note-types/2385223452/"
    assert normalize_url(url) == "https://app.ankihub.net/api/note-types/<id>/"


def test_tag_exists_for_every_suggestion_type(anki_session_with_addon: AnkiSession):
    from ankihub.suggestions import SuggestionType
    from ankihub.sync import TAG_FOR_SUGGESTION_TYPE

    for suggestion_type in SuggestionType:
        assert TAG_FOR_SUGGESTION_TYPE.get(suggestion_type, None) is not None


def test_prepared_field_content(anki_session_with_addon: AnkiSession):
    from ankihub.suggestions import _prepared_field_val

    assert _prepared_field_val('<img src="foo.jpg">') == '<img src="foo.jpg">'

    assert (
        _prepared_field_val('<img src="foo.jpg" data-editor-shrink="true">')
        == '<img src="foo.jpg">'
    )
