"""In-memory credentials shared by the Comix browser and GUI cover loader."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Iterable


@dataclass(frozen=True)
class ComixNetworkCredentials:
    """The minimum browser session state needed for static.comix.to images."""

    user_agent: str = ""
    cookies: tuple[tuple[str, str], ...] = ()
    generation: int = 0

    @property
    def cookie_header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self.cookies)

    @property
    def cookie_dict(self) -> dict[str, str]:
        return dict(self.cookies)


class _ComixCredentialStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._credentials = ComixNetworkCredentials()

    @staticmethod
    def _comix_cookie_pairs(cookies: Iterable[object]) -> tuple[tuple[str, str], ...]:
        values: dict[str, str] = {}
        for cookie in cookies:
            name = getattr(cookie, "name", None)
            value = getattr(cookie, "value", None)
            domain = str(getattr(cookie, "domain", "") or "").lstrip(".").lower()
            if (
                isinstance(name, str)
                and name
                and isinstance(value, str)
                and (domain == "comix.to" or domain.endswith(".comix.to"))
            ):
                values[name] = value
        return tuple(sorted(values.items()))

    def publish(self, user_agent: str, cookies: Iterable[object]) -> ComixNetworkCredentials:
        if not isinstance(user_agent, str):
            user_agent = ""
        pairs = self._comix_cookie_pairs(cookies)
        with self._lock:
            current = self._credentials
            if current.user_agent == user_agent and current.cookies == pairs:
                return current
            self._credentials = ComixNetworkCredentials(
                user_agent=user_agent,
                cookies=pairs,
                generation=current.generation + 1,
            )
            return self._credentials

    def snapshot(self) -> ComixNetworkCredentials:
        with self._lock:
            return self._credentials

    def clear_for_tests(self) -> None:
        with self._lock:
            self._credentials = ComixNetworkCredentials()


_credential_store = _ComixCredentialStore()


def publish_comix_credentials(
    user_agent: str,
    cookies: Iterable[object],
) -> ComixNetworkCredentials:
    """Publish browser credentials without persisting or logging cookie values."""
    return _credential_store.publish(user_agent, cookies)


def get_comix_credentials() -> ComixNetworkCredentials:
    """Return an immutable snapshot safe to use from an image-provider thread."""
    return _credential_store.snapshot()
