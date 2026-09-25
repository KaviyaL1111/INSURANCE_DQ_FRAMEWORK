"""Connector factory — resolves a named connection profile to a live connector."""
from __future__ import annotations

from src.connectors.base import Connector, ConnectionProfile, QueryResult, extract_params, split_statements
from src.connectors.flatfile_connector import FlatFileConnector
from src.connectors.mssql_connector import MSSQLConnector
from src.connectors.snowflake_connector import SnowflakeConnector

_REGISTRY = {"snowflake": SnowflakeConnector, "mssql": MSSQLConnector, "flatfile": FlatFileConnector}


def get_connector(profile_name: str | None = None) -> Connector:
    """Open a connector for a profile named in .env (default: DEFAULT_CONNECTION)."""
    from src.config import get_profile

    profile = get_profile(profile_name)
    try:
        cls = _REGISTRY[profile.kind]
    except KeyError:
        raise ValueError(
            f"Connection '{profile.name}' has unsupported kind '{profile.kind}'. "
            f"Supported: {', '.join(sorted(_REGISTRY))}"
        ) from None
    return cls(profile)


__all__ = [
    "Connector", "ConnectionProfile", "QueryResult",
    "SnowflakeConnector", "MSSQLConnector", "FlatFileConnector",
    "get_connector", "extract_params", "split_statements",
]
