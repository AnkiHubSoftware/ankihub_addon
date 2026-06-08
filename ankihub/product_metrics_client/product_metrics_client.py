from __future__ import annotations

import socket
import time
from typing import Any, Dict, Iterator, Optional, Sequence, Tuple, cast

import requests
from requests import PreparedRequest, Request, Response, Session
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .models import ProductEvent, SendEventsResponse

DEFAULT_PRODUCT_METRICS_URL = "https://product-events.ankihub.net"
STAGING_PRODUCT_METRICS_URL = "https://product-events.staging.ankihub.net"

MAX_EVENTS = 500

CONNECTION_TIMEOUT = 10
READ_TIMEOUT = 20
MAX_RETRIES = 3

REQUEST_RETRY_EXCEPTION_TYPES = (
    requests.exceptions.JSONDecodeError,
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
    ConnectionError,
    socket.gaierror,
    socket.timeout,
)


def _sanitized_response_detail(response: Response, max_len: int = 500) -> Optional[str]:
    try:
        body_text = response.text
    except Exception:
        return None
    if len(body_text) > max_len:
        return body_text[:max_len] + "... [truncated]"
    return body_text


class ProductMetricsHTTPError(Exception):
    """An unexpected HTTP code was returned by the product event collector."""

    def __init__(self, response: Response):
        self.response = response

    def __str__(self) -> str:
        summary = f"Product metrics request error: {self.response.status_code} {self.response.reason}"
        detail = _sanitized_response_detail(self.response)
        if detail is None:
            return f"{summary}\nUnable to read response content"
        if detail:
            return f"{summary}\n{detail}"
        return summary


class ProductMetricsRequestException(Exception):
    """An exception occurred while sending product metrics."""

    def __init__(self, original_exception: BaseException):
        self.original_exception = original_exception
        self.__cause__ = original_exception

    def __str__(self) -> str:
        return f"Product metrics request exception: {self.original_exception}"


class ProductMetricsClient:
    """Client for sending product analytics events to the product event collector Lambda."""

    def __init__(
        self,
        url: str,
        session: Optional[Session] = None,
    ) -> None:
        self.url = url
        self._session = session

    def track(
        self,
        distinct_id: str,
        event_name: str,
        properties: Optional[Dict[str, Any]] = None,
        timestamp: Optional[int] = None,
    ) -> SendEventsResponse:
        event = ProductEvent(
            distinct_id=distinct_id,
            event_name=event_name,
            timestamp=timestamp if timestamp is not None else int(time.time()),
            properties=properties,
        )
        return self.send_events([event])

    def send_events(self, events: Sequence[ProductEvent]) -> SendEventsResponse:
        if not events:
            raise ValueError("events must be a non-empty sequence")

        stored_total = 0
        response_date = ""

        for batch in _batched(events, MAX_EVENTS):
            response = self._send_batch(batch)
            stored_total += response.stored
            response_date = response.date

        return SendEventsResponse(stored=stored_total, date=response_date)

    def _send_batch(self, events: Sequence[ProductEvent]) -> SendEventsResponse:
        payload = {"events": [event.to_dict() for event in events]}
        response = self._post(payload)
        if response.status_code != 200:
            raise ProductMetricsHTTPError(response)
        return SendEventsResponse.from_dict(response.json())

    def _post(self, payload: Dict[str, Any]) -> Response:
        request = Request(
            method="POST",
            url=self.url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        prepared = request.prepare()
        return self._send_request_with_retry(prepared)

    def _send_request_with_retry(self, request: PreparedRequest) -> Response:
        timeout: Tuple[int, int] = (CONNECTION_TIMEOUT, READ_TIMEOUT)

        @retry(
            stop=stop_after_attempt(MAX_RETRIES),
            wait=wait_exponential(multiplier=1, max=10),
            retry=retry_if_exception_type(REQUEST_RETRY_EXCEPTION_TYPES),
        )
        def send_with_retry() -> Response:
            return self._get_session().send(request, timeout=timeout)

        try:
            return send_with_retry()
        except RetryError as error:
            last_attempt = cast(Any, error.last_attempt)
            try:
                return last_attempt.result()
            except Exception as exc:
                raise ProductMetricsRequestException(exc) from exc

    def _get_session(self) -> Session:
        if self._session is None:
            self._session = Session()
        return self._session


def _batched(events: Sequence[ProductEvent], batch_size: int) -> Iterator[Sequence[ProductEvent]]:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    for index in range(0, len(events), batch_size):
        yield events[index : index + batch_size]
