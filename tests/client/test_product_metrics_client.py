import os

import pytest
from requests_mock import Mocker

os.environ["SKIP_INIT"] = "1"

from ankihub.product_metrics_client import (
    MAX_EVENTS,
    ProductEvent,
    ProductMetricsClient,
    ProductMetricsHTTPError,
    SendEventsResponse,
)


def test_product_event_to_dict_omits_none_properties() -> None:
    event = ProductEvent(distinct_id="user-1", event_name="opened", timestamp=100)
    assert event.to_dict() == {
        "distinct_id": "user-1",
        "event_name": "opened",
        "timestamp": 100,
    }


def test_product_event_to_dict_includes_properties() -> None:
    event = ProductEvent(
        distinct_id="user-1",
        event_name="opened",
        timestamp=100,
        properties={"deck_id": 42},
    )
    assert event.to_dict() == {
        "distinct_id": "user-1",
        "event_name": "opened",
        "timestamp": 100,
        "properties": {"deck_id": 42},
    }


def test_send_events_posts_payload(requests_mock: Mocker) -> None:
    url = "https://product-events.example.com"
    requests_mock.post(url, json={"stored": 1, "date": "2026-06-03"}, status_code=200)

    client = ProductMetricsClient(url=url)
    response = client.send_events([ProductEvent(distinct_id="user-1", event_name="tutorial_started", timestamp=123)])

    assert response == SendEventsResponse(stored=1, date="2026-06-03")
    assert requests_mock.call_count == 1
    assert requests_mock.last_request.json() == {
        "events": [
            {
                "distinct_id": "user-1",
                "event_name": "tutorial_started",
                "timestamp": 123,
            }
        ]
    }


def test_track_sends_single_event(requests_mock: Mocker, mocker) -> None:
    url = "https://product-events.example.com"
    requests_mock.post(url, json={"stored": 1, "date": "2026-06-03"}, status_code=200)
    mocker.patch("ankihub.product_metrics_client.product_metrics_client.time.time", return_value=456)

    client = ProductMetricsClient(url=url)
    response = client.track("user-1", "tutorial_step_viewed", properties={"step": 2})

    assert response.stored == 1
    assert requests_mock.last_request.json() == {
        "events": [
            {
                "distinct_id": "user-1",
                "event_name": "tutorial_step_viewed",
                "timestamp": 456,
                "properties": {"step": 2},
            }
        ]
    }


def test_send_events_batches_when_exceeding_max_events(requests_mock: Mocker) -> None:
    url = "https://product-events.example.com"

    def _response(request, context):
        stored = len(request.json()["events"])
        return {"stored": stored, "date": "2026-06-03"}

    requests_mock.post(url, json=_response, status_code=200)

    client = ProductMetricsClient(url=url)
    events = [
        ProductEvent(distinct_id=f"user-{index}", event_name="opened", timestamp=index)
        for index in range(MAX_EVENTS + 2)
    ]

    response = client.send_events(events)

    assert response == SendEventsResponse(stored=MAX_EVENTS + 2, date="2026-06-03")
    assert requests_mock.call_count == 2
    assert len(requests_mock.request_history[0].json()["events"]) == MAX_EVENTS
    assert len(requests_mock.request_history[1].json()["events"]) == 2


def test_send_events_raises_for_empty_sequence() -> None:
    client = ProductMetricsClient(url="https://product-events.example.com")
    with pytest.raises(ValueError, match="events must be a non-empty sequence"):
        client.send_events([])


def test_send_events_raises_http_error(requests_mock: Mocker) -> None:
    url = "https://product-events.example.com"
    requests_mock.post(
        url,
        json={"error": "Validation failed", "details": ["missing top-level 'events' key"]},
        status_code=400,
    )

    client = ProductMetricsClient(url=url)
    with pytest.raises(ProductMetricsHTTPError) as exc_info:
        client.send_events([ProductEvent(distinct_id="user-1", event_name="opened", timestamp=1)])

    assert exc_info.value.response.status_code == 400
