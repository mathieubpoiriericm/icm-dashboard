"""Shared fixtures for pipeline tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path before any pipeline imports
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import asyncio  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Any  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402

from pipeline.config import PipelineConfig  # noqa: E402
from pipeline.llm_extraction import GeneEntry  # noqa: E402
from pipeline.quality_metrics import PipelineMetrics, TokenUsage  # noqa: E402

# ---------------------------------------------------------------------------
# GeneEntry factory
# ---------------------------------------------------------------------------


@pytest.fixture
def make_gene_entry():
    """Factory fixture for GeneEntry with sensible defaults."""

    def _make(
        gene_symbol: str = "NOTCH3",
        protein_name: str | None = "Notch receptor 3",
        gwas_trait: list[str] | None = None,
        mendelian_randomization: bool = False,
        omics_evidence: list[str] | None = None,
        confidence: float = 0.9,
        causal_evidence_summary: str | None = "Strong GWAS association",
        pmid: str = "12345678",
    ) -> GeneEntry:
        return GeneEntry(
            gene_symbol=gene_symbol,
            protein_name=protein_name,
            gwas_trait=gwas_trait or [],
            mendelian_randomization=mendelian_randomization,
            omics_evidence=omics_evidence or [],
            confidence=confidence,
            causal_evidence_summary=causal_evidence_summary,
            pmid=pmid,
        )

    return _make


@pytest.fixture
def sample_gene_entry(make_gene_entry):
    """A single GeneEntry with typical values."""
    return make_gene_entry()


@pytest.fixture
def sample_gene_entries(make_gene_entry):
    """Multiple GeneEntry instances for batch testing."""
    return [
        make_gene_entry(gene_symbol="NOTCH3", pmid="11111111"),
        make_gene_entry(
            gene_symbol="HTRA1",
            protein_name="Serine protease HTRA1",
            gwas_trait=["WMH"],
            confidence=0.95,
            pmid="22222222",
        ),
        make_gene_entry(
            gene_symbol="COL4A1",
            protein_name="Collagen type IV alpha 1",
            gwas_trait=["SVS", "WMH"],
            omics_evidence=["TWAS"],
            confidence=0.85,
            pmid="33333333",
        ),
    ]


# ---------------------------------------------------------------------------
# PipelineConfig variants
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    """Default pipeline config."""
    return PipelineConfig()


@pytest.fixture
def strict_config():
    """Config with strict thresholds for testing edge cases."""
    return PipelineConfig(
        confidence_threshold=0.9,
        max_retries=1,
        max_rate_limit_retries=1,
    )


@pytest.fixture
def fast_config():
    """Config with low limits for fast testing."""
    return PipelineConfig(
        max_concurrent_papers=2,
        rpm_limit=5,
        tpm_limit=10_000,
        estimated_tokens_per_call=1000,
    )


# ---------------------------------------------------------------------------
# Metrics fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_metrics():
    """Fresh PipelineMetrics with all zeros."""
    return PipelineMetrics()


@pytest.fixture
def populated_metrics():
    """PipelineMetrics with realistic values."""
    return PipelineMetrics(
        papers_processed=10,
        fulltext_retrieved=7,
        abstract_only=3,
        genes_extracted=25,
        genes_validated=20,
        genes_rejected=5,
        token_usage=TokenUsage(
            input_tokens=50_000,
            output_tokens=10_000,
            cache_creation_input_tokens=5_000,
            cache_read_input_tokens=15_000,
        ),
    )


# ---------------------------------------------------------------------------
# Mock Anthropic helpers
# ---------------------------------------------------------------------------


@dataclass
class MockUsage:
    """Mimics anthropic response.usage."""

    input_tokens: int = 1000
    output_tokens: int = 500
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class MockTextBlock:
    """Mimics a text content block."""

    type: str = "text"
    text: str = ""


@dataclass
class MockThinkingBlock:
    """Mimics a thinking content block."""

    type: str = "thinking"
    thinking: str = "reasoning..."


@dataclass
class MockAnthropicResponse:
    """Mimics an Anthropic message response."""

    usage: MockUsage | None = None
    content: list[Any] | None = None
    parsed_output: Any = None
    stop_reason: str = "end_turn"

    def __post_init__(self):
        if self.usage is None:
            self.usage = MockUsage()
        if self.content is None:
            self.content = [MockTextBlock(text="{}")]


@pytest.fixture
def mock_anthropic_response():
    """Factory for mock Anthropic responses."""

    def _make(
        text: str = '{"genes": []}',
        input_tokens: int = 1000,
        output_tokens: int = 500,
        include_thinking: bool = False,
    ) -> MockAnthropicResponse:
        content: list[Any] = []
        if include_thinking:
            content.append(MockThinkingBlock())
        content.append(MockTextBlock(text=text))
        return MockAnthropicResponse(
            usage=MockUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            content=content,
        )

    return _make


@pytest.fixture
def mock_stream_context(mock_anthropic_response):
    """Factory for mock streaming context manager."""

    def _make(response: MockAnthropicResponse | None = None):
        if response is None:
            response = mock_anthropic_response()
        stream = AsyncMock()
        stream.__aenter__.return_value = stream
        stream.__aexit__.return_value = None
        stream.get_final_message = AsyncMock(return_value=response)
        return stream

    return _make


# ---------------------------------------------------------------------------
# Autouse fixtures to reset module-level singletons
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_validation_cache():
    """Clear the NCBI gene cache after each test."""
    from collections import OrderedDict

    yield
    import pipeline.validation as val

    val._gene_cache = OrderedDict()


@pytest.fixture(autouse=True)
def _reset_llm_client():
    """Clear the shared Anthropic client after each test."""
    yield
    import pipeline.llm_extraction as llm

    llm._async_client = None


@pytest.fixture(autouse=True)
def _reset_validation_client():
    """Clear the shared validation HTTP client and throttle state after each test."""
    yield
    import pipeline.validation as val

    val._client_manager.reset()
    val._ncbi_semaphore = None
    val._last_request_time = 0.0
    val._throttle_lock = None
    val._cache_lock = None
    val._validation_state_initialized = False


@pytest.fixture(autouse=True)
def _reset_database_singleton():
    """Clear the Database singleton state after each test."""
    yield
    from pipeline.database import Database

    Database._pool = None
    Database._config = None


@pytest.fixture(autouse=True)
def _reset_pdf_client():
    """Clear the shared pdf_retrieval HTTP client after each test."""
    yield
    import pipeline.pdf_retrieval as pdf

    pdf._http_client = None


@pytest.fixture(autouse=True)
def _reset_pubmed_config():
    """Reset Entrez configured flag after each test."""
    yield
    import pipeline.pubmed_search as ps

    ps._entrez_configured = False


@pytest.fixture(autouse=True)
def _reset_main_client():
    """Clear the metadata client in main after each test."""
    yield
    import pipeline.main as m

    m._metadata_client = None


@pytest.fixture(autouse=True)
def _reset_ncbi_gene_client():
    """Clear the shared ncbi_gene_fetch HTTP client and cache after each test."""
    from collections import OrderedDict

    yield
    import pipeline.ncbi_gene_fetch as ncbi

    ncbi._client_manager.reset()
    ncbi._gene_cache = OrderedDict()
    ncbi._ncbi_semaphore = None
    ncbi._cache_lock = None
    ncbi._ncbi_fetch_state_initialized = False


@pytest.fixture(autouse=True)
def _reset_uniprot_client():
    """Clear the shared uniprot_fetch HTTP client and cache after each test."""
    from collections import OrderedDict

    yield
    import pipeline.uniprot_fetch as uni

    uni._client_manager.reset()
    uni._uniprot_cache = OrderedDict()
    uni._uniprot_semaphore = None
    uni._cache_lock = None


@pytest.fixture(autouse=True)
def _reset_pubmed_citations_client():
    """Clear the shared pubmed_citations HTTP client and cache after each test."""
    from collections import OrderedDict

    yield
    import pipeline.pubmed_citations as pc

    pc._client_manager.reset()
    pc._citation_cache = OrderedDict()
    pc._ncbi_semaphore = None
    pc._cache_lock = None


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
