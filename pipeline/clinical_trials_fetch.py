"""ClinicalTrials.gov (CTG) v2 discovery and refresh module.

Searches CTG for cSVD-relevant drug trials, maps the JSON studies to flat
records, and upserts them into the ``clinical_trials`` Postgres table.

The upsert is intentionally write-only for API-sourced columns. Curator-owned
columns (mechanism_of_action, genetic_target, genetic_evidence,
svd_population, svd_population_details) are never populated here — they
default to NULL on INSERT and are omitted from the ON CONFLICT update set,
so existing curator edits are preserved across runs.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Final

import httpx

from pipeline.cache_utils import SyncResult
from pipeline.config import PipelineConfig
from pipeline.http_client import AsyncHttpClientManager
from pipeline.rate_limiter import compute_backoff

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

CTG_BASE_URL: Final[str] = "https://clinicaltrials.gov/api/v2"
CTG_STUDIES_URL: Final[str] = f"{CTG_BASE_URL}/studies"

# CTG v2 intervention types. We only want drug trials — skip behavioral,
# device, procedure, etc.
DRUG_INTERVENTION_TYPES: Final[frozenset[str]] = frozenset({"DRUG"})

# Map CTG v2 raw phase enum values to the display labels the Shiny filter
# expects ("Phase 2" not "PHASE2"). Unmapped values pass through verbatim.
_PHASE_DISPLAY_MAP: Final[dict[str, str]] = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "N/A",
}


# ---------------------------------------------------------------------------
# MODELS
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClinicalTrialRecord:
    """Flat CTG-derived record for the ``clinical_trials`` table.

    Only API-sourced columns are represented here. Curator-owned columns
    are handled at the SQL layer (NULL on INSERT; excluded from UPDATE).
    """

    drug: str
    trial_name: str | None
    registry_id: str
    clinical_trial_phase: str | None
    target_sample_size: int | None
    estimated_completion_date: str | None
    primary_outcome: str | None
    sponsor_type: str | None


# ---------------------------------------------------------------------------
# HTTP CLIENT AND CONCURRENCY
# ---------------------------------------------------------------------------

_client_manager = AsyncHttpClientManager(timeout=30.0)
_ctg_semaphore: asyncio.Semaphore | None = None


def init_ctg_fetch_state(config: PipelineConfig | None = None) -> None:
    """Eagerly initialize the CTG concurrency semaphore.

    Must be called once from inside the running event loop. Idempotent.
    """
    global _ctg_semaphore
    if _ctg_semaphore is not None:
        return
    # asyncio.Semaphore silently ties itself to whatever loop happens to be
    # current, so mis-use (sync context) only surfaces much later as
    # "attached to a different loop" errors. Fail fast instead.
    try:
        asyncio.get_running_loop()
    except RuntimeError as e:
        raise RuntimeError(
            "init_ctg_fetch_state() must be called from inside a running event loop"
        ) from e
    _ctg_semaphore = asyncio.Semaphore((config or PipelineConfig()).ct_max_concurrency)


def _get_ctg_semaphore() -> asyncio.Semaphore:
    """Get or lazily create the CTG concurrency semaphore."""
    if _ctg_semaphore is None:
        init_ctg_fetch_state()
    assert _ctg_semaphore is not None
    return _ctg_semaphore


async def close_ctg_client() -> None:
    """Close the shared CTG HTTP client (call at shutdown)."""
    await _client_manager.close()


# ---------------------------------------------------------------------------
# JSON → RECORD MAPPING
# ---------------------------------------------------------------------------


def _get_path(obj: Any, *path: str) -> Any:
    """Safely traverse nested dicts; return None on missing keys."""
    current = obj
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def _nonempty_str(value: Any) -> str | None:
    """Return non-empty strings unchanged and normalize other values to None."""
    return value if isinstance(value, str) and value else None


def _first_primary_outcome(study: dict[str, Any]) -> str | None:
    """Extract the first primary outcome measure, if any."""
    outcomes = _get_path(study, "protocolSection", "outcomesModule", "primaryOutcomes")
    if not isinstance(outcomes, list) or not outcomes:
        return None
    first = outcomes[0]
    if not isinstance(first, dict):
        return None
    return _nonempty_str(first.get("measure"))


def _first_phase(study: dict[str, Any]) -> str | None:
    """Extract the first phase label mapped to display form (``"Phase 2"``)."""
    phases = _get_path(study, "protocolSection", "designModule", "phases")
    if not isinstance(phases, list) or not phases:
        return None
    if (first := _nonempty_str(phases[0])) is None:
        return None
    return _PHASE_DISPLAY_MAP.get(first, first)


def _drug_interventions(study: dict[str, Any]) -> list[str]:
    """Extract DRUG-type intervention names from a CTG v2 study."""
    interventions = _get_path(
        study, "protocolSection", "armsInterventionsModule", "interventions"
    )
    if not isinstance(interventions, list):
        return []
    drugs: list[str] = []
    seen: set[str] = set()
    for item in interventions:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        name = item.get("name")
        if not isinstance(itype, str) or itype.upper() not in DRUG_INTERVENTION_TYPES:
            continue
        if not isinstance(name, str):
            continue
        stripped = name.strip()
        if stripped and stripped not in seen:
            drugs.append(stripped)
            seen.add(stripped)
    return drugs


def _map_study_to_records(study: dict[str, Any]) -> list[ClinicalTrialRecord]:
    """Map a CTG v2 study dict to zero or more ClinicalTrialRecord rows.

    One record per DRUG-type intervention. Non-drug interventions are
    skipped. Trials with no drug interventions yield no records.
    """
    nct_id = _get_path(study, "protocolSection", "identificationModule", "nctId")
    if not isinstance(nct_id, str) or not nct_id:
        return []

    drugs = _drug_interventions(study)
    if not drugs:
        return []

    trial_name = _get_path(
        study, "protocolSection", "identificationModule", "briefTitle"
    )
    phase = _first_phase(study)

    enrollment = _get_path(
        study, "protocolSection", "designModule", "enrollmentInfo", "count"
    )
    sample_size = enrollment if isinstance(enrollment, int) else None

    completion = _get_path(
        study, "protocolSection", "statusModule", "completionDateStruct", "date"
    )
    primary_outcome = _first_primary_outcome(study)

    sponsor_type = _get_path(
        study,
        "protocolSection",
        "sponsorCollaboratorsModule",
        "leadSponsor",
        "class",
    )
    return [
        ClinicalTrialRecord(
            drug=drug,
            trial_name=_nonempty_str(trial_name),
            registry_id=nct_id,
            clinical_trial_phase=phase,
            target_sample_size=sample_size,
            estimated_completion_date=_nonempty_str(completion),
            primary_outcome=primary_outcome,
            sponsor_type=_nonempty_str(sponsor_type),
        )
        for drug in drugs
    ]


# ---------------------------------------------------------------------------
# FETCH FUNCTIONS
# ---------------------------------------------------------------------------


async def _fetch_page_with_retry(
    params: dict[str, str],
    max_retries: int,
) -> dict[str, Any]:
    """Fetch a single CTG /studies page, retrying on 429/5xx or transient errors.

    The concurrency semaphore is held only around the HTTP call — backoff
    sleeps run outside it so one failing request cannot starve concurrent
    peers waiting for a slot.
    """
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            async with _get_ctg_semaphore():
                client = await _client_manager.get()
                resp = await client.get(CTG_STUDIES_URL, params=params)
            if resp.status_code == 200:
                body: dict[str, Any] = resp.json()
                return body
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                logger.warning(
                    f"CTG returned {resp.status_code} "
                    f"(attempt {attempt + 1}/{max_retries + 1})"
                )
                last_error = httpx.HTTPStatusError(
                    f"CTG HTTP {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            else:
                # Non-retryable (4xx other than 429); raise_for_status handles
                # 4xx/5xx, and we raise explicitly for the rare 3xx/other case.
                resp.raise_for_status()
                raise httpx.HTTPStatusError(
                    f"Unexpected CTG status {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
        except (httpx.TimeoutException, httpx.RequestError, json.JSONDecodeError) as e:
            logger.warning(
                f"CTG transient error (attempt {attempt + 1}/{max_retries + 1}): {e}"
            )
            last_error = e

        if attempt < max_retries:
            # 1s, 2s, 4s, ... with ±25% jitter, capped at the shared module cap
            # so raising max_retries can't stall fetches for minutes.
            await asyncio.sleep(compute_backoff(1.0, attempt + 1))

    raise last_error or RuntimeError(
        f"CTG fetch exhausted {max_retries + 1} attempts with no error captured"
    )


async def _search_condition_term(
    term: str,
    page_size: int,
    max_retries: int,
) -> list[dict[str, Any]]:
    """Paginated search for a single condition term.

    Returns the concatenated list of study dicts across all pages. If a page
    fails after all retries, earlier pages' studies are preserved — only the
    failed page (and any beyond it) are lost.
    """
    collected: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params: dict[str, str] = {
            "query.cond": term,
            "pageSize": str(page_size),
            "format": "json",
        }
        if page_token:
            params["pageToken"] = page_token

        try:
            body = await _fetch_page_with_retry(params, max_retries)
        except Exception as e:
            logger.warning(
                f"CTG term {term!r} pagination aborted after "
                f"{len(collected)} studies: {e}"
            )
            break

        studies = body.get("studies")
        if isinstance(studies, list):
            collected.extend(s for s in studies if isinstance(s, dict))

        next_token = body.get("nextPageToken")
        if not isinstance(next_token, str) or not next_token:
            break
        page_token = next_token

    logger.info(f"CTG term {term!r}: {len(collected)} studies")
    return collected


async def fetch_csvd_studies(
    search_terms: tuple[str, ...] | list[str],
    page_size: int,
    max_retries: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Search CTG across all cSVD-relevant terms and deduplicate by NCT ID.

    Returns (studies, errors). A failure in one term does not abort the rest;
    failed terms appear in the errors list while successful terms contribute
    their studies.
    """
    if not search_terms:
        return [], []

    results = await asyncio.gather(
        *(
            _search_condition_term(term, page_size, max_retries)
            for term in search_terms
        ),
        return_exceptions=True,
    )

    deduped: dict[str, dict[str, Any]] = {}
    term_errors: list[str] = []
    for term, result in zip(search_terms, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning(f"CTG term {term!r} failed: {result}")
            term_errors.append(f"CTG term {term!r}: {result}")
            continue
        for study in result:
            nct = _get_path(study, "protocolSection", "identificationModule", "nctId")
            if isinstance(nct, str) and nct:
                deduped.setdefault(nct, study)

    logger.info(
        f"CTG: {len(deduped)} unique cSVD-relevant studies "
        f"across {len(search_terms)} search terms "
        f"({len(term_errors)} term failures)"
    )
    return list(deduped.values()), term_errors


# ---------------------------------------------------------------------------
# DATABASE SYNC
# ---------------------------------------------------------------------------


async def sync_clinical_trials(config: PipelineConfig) -> SyncResult:
    """Discover + refresh cSVD trials in the clinical_trials table.

    1. Search CTG for each configured term, paginated, deduplicated by NCT ID.
    2. Map studies to one record per DRUG-type intervention.
    3. Single-statement upsert — curator-owned columns are excluded from the
       UPDATE SET, so they default to NULL on INSERT and are preserved on
       CONFLICT.

    Returns a SyncResult with counts. ``fetched`` = distinct NCT studies
    hit; ``cached`` = rows upserted; ``failed`` = mapping/insert errors.
    """
    from pipeline.database import upsert_clinical_trials_batch

    init_ctg_fetch_state(config)

    try:
        studies, term_errors = await fetch_csvd_studies(
            search_terms=config.ct_search_terms,
            page_size=config.ct_page_size,
            max_retries=config.ct_max_retries,
        )
    except Exception as e:
        logger.exception("CTG fetch failed")
        return SyncResult(fetched=0, cached=0, failed=0, errors=[f"CTG fetch: {e}"])

    errors = term_errors.copy()

    records: list[ClinicalTrialRecord] = []
    studies_without_nct = 0
    studies_without_drug = 0
    for study in studies:
        try:
            nct = _get_path(study, "protocolSection", "identificationModule", "nctId")
            if not isinstance(nct, str) or not nct:
                studies_without_nct += 1
                continue
            mapped = _map_study_to_records(study)
            if not mapped:
                studies_without_drug += 1
            records.extend(mapped)
        except Exception as e:  # defensive — malformed study dict
            nct = _get_path(study, "protocolSection", "identificationModule", "nctId")
            errors.append(f"CTG map {nct or '?'}: {e}")

    if studies_without_nct:
        logger.debug(f"CTG: {studies_without_nct} studies dropped (missing NCT ID)")
    if studies_without_drug:
        logger.info(
            f"CTG: {studies_without_drug}/{len(studies)} studies had "
            "no DRUG-type intervention (skipped)"
        )

    try:
        upserted = await upsert_clinical_trials_batch(records)
    except Exception as e:
        logger.exception("CTG upsert failed")
        errors.append(f"CTG upsert: {e}")
        return SyncResult(
            fetched=len(studies),
            cached=0,
            failed=len(records),
            errors=errors,
        )

    logger.info(
        f"CTG sync: {len(studies)} studies fetched, "
        f"{upserted} records upserted (errors={len(errors)})"
    )
    return SyncResult(
        fetched=len(studies),
        cached=upserted,
        failed=len(errors),
        errors=errors,
    )
