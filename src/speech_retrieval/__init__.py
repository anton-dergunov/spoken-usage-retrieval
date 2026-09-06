"""Public library surface for spoken usage retrieval."""

__version__ = "0.1.0"

from .analysis import InvalidAnalysisError, UnsupportedAnalysisError
from .api import create_app
from .channels import (
    ChannelConflictError,
    ChannelNotFoundError,
    ChannelRepository,
    ChannelRepositoryError,
)
from .contracts import (
    AnalyzerInfo,
    ChannelCreate,
    ChannelRecord,
    ChannelStatistics,
    ChannelUpdate,
    Clip,
    CorpusStatistics,
    CorpusStatus,
    DoctorReport,
    LanguageStatistics,
    LanguageStatus,
    LanguageUpdate,
    ModelRecord,
    SearchResponse,
    SearchResult,
    Suggestion,
    SuggestionsResponse,
    TokenInfo,
    UpdateSummary,
)
from .search import Corpus, IncompatibleIndexError, SearchError
from .service import Indexer
from .settings import Settings

__all__ = [
    "AnalyzerInfo",
    "ChannelCreate",
    "ChannelConflictError",
    "ChannelNotFoundError",
    "ChannelRecord",
    "ChannelRepository",
    "ChannelRepositoryError",
    "ChannelStatistics",
    "ChannelUpdate",
    "Clip",
    "Corpus",
    "CorpusStatistics",
    "CorpusStatus",
    "DoctorReport",
    "IncompatibleIndexError",
    "Indexer",
    "InvalidAnalysisError",
    "LanguageStatistics",
    "LanguageStatus",
    "LanguageUpdate",
    "ModelRecord",
    "SearchError",
    "SearchResponse",
    "SearchResult",
    "Settings",
    "Suggestion",
    "SuggestionsResponse",
    "TokenInfo",
    "UnsupportedAnalysisError",
    "UpdateSummary",
    "create_app",
]
