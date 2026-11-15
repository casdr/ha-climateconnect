"""Shared fixtures for the ClimateConnect tests."""

from __future__ import annotations

import annotationlib
import inspect
import types
import unittest.mock
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.climateconnect.const import DOMAIN

from .fake_client import FakeClient
from .helpers import EMAIL, PASSWORD

if TYPE_CHECKING:
    from collections.abc import Generator


class _ForwardRefInspect(types.ModuleType):
    """`inspect` for unittest.mock that leaves unresolvable annotations as forward refs.

    pytest-homeassistant-custom-component autospecs recorder functions whose annotations
    name classes imported only under TYPE_CHECKING. On Python 3.14.2, autospec evaluates
    those annotations and fails with NameError.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(inspect, name)

    @staticmethod
    def signature(obj: Any, **kwargs: Any) -> inspect.Signature:
        """Return the signature, leaving unresolvable annotations unevaluated."""
        kwargs.setdefault("annotation_format", annotationlib.Format.FORWARDREF)
        return inspect.signature(obj, **kwargs)


unittest.mock.inspect = _ForwardRefInspect("inspect")  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(  # pylint: disable=unused-argument
    recorder_mock: Any, enable_custom_integrations: None
) -> None:
    """Load the integration from custom_components, with the recorder it depends on.

    The recorder has to be set up before `hass`, so it is requested here first.
    """


@pytest.fixture(name="client")
def client_fixture() -> FakeClient:
    """Return a fake client with one boiler."""
    return FakeClient()


@pytest.fixture(name="mock_client")
def mock_client_fixture(client: FakeClient) -> Generator[FakeClient]:
    """Use the fake client for config entries and the config flow."""
    with (
        patch(
            "custom_components.climateconnect.ClimateConnectClient",
            return_value=client,
        ),
        patch(
            "custom_components.climateconnect.config_flow.ClimateConnectClient",
            return_value=client,
        ),
    ):
        yield client


@pytest.fixture(name="config_entry")
def config_entry_fixture() -> MockConfigEntry:
    """Return a configured ClimateConnect entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={"email": EMAIL, "password": PASSWORD},
    )
