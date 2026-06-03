"""Client for sending product analytics events to the product event collector Lambda."""

from .models import ProductEvent, SendEventsResponse
from .product_metrics_client import (
    DEFAULT_PRODUCT_METRICS_URL,
    MAX_EVENTS,
    STAGING_PRODUCT_METRICS_URL,
    ProductMetricsClient,
    ProductMetricsHTTPError,
    ProductMetricsRequestException,
)

__all__ = [
    "DEFAULT_PRODUCT_METRICS_URL",
    "MAX_EVENTS",
    "STAGING_PRODUCT_METRICS_URL",
    "ProductEvent",
    "ProductMetricsClient",
    "ProductMetricsHTTPError",
    "ProductMetricsRequestException",
    "SendEventsResponse",
]
