"""Subprocess entry point for HPC vLLM pipeline runs."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pipeline_app_hpc.cli",
        description="HPC vLLM pipeline (local PDFs only).",
    )
    p.add_argument(
        "--local-pdfs",
        type=Path,
        required=True,
        metavar="PATH",
        help="PDF file or directory of PDFs to process",
    )
    p.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip NCBI gene validation",
    )
    return p


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def build_pipeline_config():
    """Build a `pipeline.config.PipelineConfig`.

    All PIPELINE_* env vars are read directly by ``PipelineConfig``'s
    ``field(default_factory=...)`` lambdas at construction; ``__post_init__``
    then derives prompt_version / max_concurrent_papers for ollama. A separate
    setattr override table here would clobber those derived values.
    """
    from pipeline.config import PipelineConfig

    return PipelineConfig()


def build_provider():
    """Construct a VllmProvider from VLLM_* env vars."""
    from pipeline_app_hpc.providers.vllm_provider import VllmProvider

    base_url = os.environ.get("VLLM_BASE_URL")
    model = os.environ.get("VLLM_MODEL")
    if not base_url:
        sys.exit("VLLM_BASE_URL env var is required")
    if not model:
        sys.exit("VLLM_MODEL env var is required")
    return VllmProvider(
        base_url=base_url,
        model=model,
        base_model_name=os.environ.get("VLLM_BASE_MODEL_NAME", model),
        adapter_name=os.environ.get("VLLM_ADAPTER_NAME", ""),
        max_model_len=_env_int("VLLM_MAX_MODEL_LEN", 0),
        quantization=os.environ.get("VLLM_QUANTIZATION", "bitsandbytes"),
    )


async def _amain(argv: list[str] | None = None) -> int:
    from pipeline_app_hpc.extract import run

    args = _build_parser().parse_args(argv)
    config = build_pipeline_config()
    provider = build_provider()
    log_dir = Path(os.environ.get("PIPELINE_LOG_DIR", "logs"))
    try:
        await run(
            provider=provider,
            pdf_dir=args.local_pdfs,
            config=config,
            skip_validation=args.skip_validation,
            log_dir=log_dir,
        )
        return 0
    except Exception:
        logger.exception("Pipeline run failed")
        return 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())
