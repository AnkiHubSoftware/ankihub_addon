"""Nottorney API client for deck purchases and downloads."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import structlog

LOGGER = structlog.stdlib.get_logger("nottorney")

NOTTORNEY_API_URL = "https://tpsaalbgdfjtzsnwswki.supabase.co/functions/v1/addon-auth"

CONNECTION_TIMEOUT = 10
STANDARD_READ_TIMEOUT = 30
LONG_READ_TIMEOUT = 600  # For file downloads


class NottorneyHTTPError(Exception):
    """An unexpected HTTP code was returned in response to a request by the Nottorney client."""

    def __init__(self, response: requests.Response):
        self.response = response
        self.status_code = response.status_code

    def __str__(self):
        return f"Nottorney API error: {self.status_code} {self.response.reason}"


class NottorneyClient:
    """Client for interacting with the Nottorney API."""

    def __init__(self, token: Optional[str] = None):
        self.token = token
        self.api_url = NOTTORNEY_API_URL

    def _get_headers(self, include_auth: bool = True) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if include_auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate user and get access token + purchased decks.

        Args:
            email: User email address
            password: User password

        Returns:
            {
                "success": True,
                "access_token": "...",
                "user": {"id": "...", "email": "...", "display_name": "..."},
                "purchased_decks": [...]
            }

        Raises:
            NottorneyHTTPError: If login fails
        """
        LOGGER.info("Logging in user", email=email)
        response = requests.post(
            f"{self.api_url}/login",
            json={"email": email, "password": password},
            headers=self._get_headers(include_auth=False),
            timeout=(CONNECTION_TIMEOUT, STANDARD_READ_TIMEOUT),
        )

        if response.status_code != 200:
            LOGGER.error("Login failed", status_code=response.status_code)
            raise NottorneyHTTPError(response)

        data = response.json()
        self.token = data.get("access_token")
        purchased_decks_count = len(data.get("purchased_decks", []))
        LOGGER.info("Login successful", purchased_decks_count=purchased_decks_count)
        return data

    def get_purchased_decks(self) -> List[Dict[str, Any]]:
        """
        Fetch user's purchased decks.

        Returns:
            List of deck objects with: id, title, description, category, card_count, apkg_path

        Raises:
            ValueError: If not authenticated
            NottorneyHTTPError: If request fails
        """
        if not self.token:
            raise ValueError("Not authenticated. Call login() first.")

        LOGGER.info("Fetching purchased decks")
        response = requests.get(
            f"{self.api_url}/decks",
            headers=self._get_headers(),
            timeout=(CONNECTION_TIMEOUT, STANDARD_READ_TIMEOUT),
        )

        if response.status_code != 200:
            raise NottorneyHTTPError(response)

        data = response.json()
        purchased_decks = data.get("purchased_decks", [])
        LOGGER.info("Fetched purchased decks", count=len(purchased_decks))
        return purchased_decks

    def get_download_url(self, product_id: str) -> Dict[str, Any]:
        """
        Get a signed download URL for a purchased deck.

        Args:
            product_id: UUID of the product/deck

        Returns:
            {
                "success": True,
                "download_url": "https://...",
                "deck_title": "...",
                "expires_in": 3600
            }

        Raises:
            ValueError: If not authenticated
            NottorneyHTTPError: If request fails (403 if not purchased, 401 if not authenticated)
        """
        if not self.token:
            raise ValueError("Not authenticated. Call login() first.")

        LOGGER.info("Getting download URL for product", product_id=product_id)
        response = requests.post(
            f"{self.api_url}/download",
            json={"product_id": product_id},
            headers=self._get_headers(),
            timeout=(CONNECTION_TIMEOUT, STANDARD_READ_TIMEOUT),
        )

        if response.status_code == 403:
            LOGGER.error("User has not purchased this deck", product_id=product_id)
            raise NottorneyHTTPError(response)
        if response.status_code != 200:
            raise NottorneyHTTPError(response)

        data = response.json()
        LOGGER.info("Got download URL", deck_title=data.get("deck_title"))
        return data

    def download_deck(self, product_id: str, save_path: Path) -> Path:
        """
        Download a deck file to the specified path.

        Args:
            product_id: UUID of the product/deck
            save_path: Path where to save the .apkg file

        Returns:
            Path to the downloaded file

        Raises:
            ValueError: If not authenticated
            NottorneyHTTPError: If download fails
        """
        url_data = self.get_download_url(product_id)
        download_url = url_data["download_url"]
        deck_title = url_data.get("deck_title", "Unknown")

        LOGGER.info("Downloading deck", deck_title=deck_title, product_id=product_id)
        response = requests.get(download_url, stream=True, timeout=(CONNECTION_TIMEOUT, LONG_READ_TIMEOUT))

        if response.status_code != 200:
            raise NottorneyHTTPError(response)

        # Ensure parent directory exists
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # Download file in chunks
        with open(save_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        file_size = save_path.stat().st_size
        LOGGER.info("Deck downloaded", deck_title=deck_title, file_size=file_size, path=str(save_path))
        return save_path

    def signout(self) -> None:
        """Clear the authentication token."""
        self.token = None
        LOGGER.info("User signed out")

