class MarketFetchError(Exception):
    """Base exception for expected collector failures."""


class SourceError(MarketFetchError):
    """A market data source failed or returned malformed data."""


class ValidationError(MarketFetchError):
    """Market data failed consistency checks."""


class CalendarCoverageError(MarketFetchError):
    """The bundled exchange calendar does not cover the requested year."""
