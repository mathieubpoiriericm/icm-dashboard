"""Fine-tune Gemma 4 31B with QLoRA on a single A100 80GB.

Reads MLX-LM-format chat JSONL produced by build_dataset.py and trains a LoRA
adapter via Unsloth + TRL SFTTrainer. Designed for single-GPU sbatch invocation
on the ICM HPC; see scripts/finetune/icm_finetune.sbatch.

USR1 from the parent sbatch (sent ~120 s before --time runs out, via
--signal=B:USR1@120) flushes a final checkpoint and exits cleanly so the job
lands a usable adapter even when wall-time clips it.

Usage:
  python scripts/finetune/train_unsloth.py \\
      --base-model unsloth/gemma-4-31b-it-bnb-4bit \\
      --train-jsonl /dev/shm/$USER/$SLURM_JOB_ID/train.jsonl \\
      --valid-jsonl /dev/shm/$USER/$SLURM_JOB_ID/valid.jsonl \\
      --output-dir  models/lora_adapters/svd_v1_31b/
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

import torch
from datasets import load_dataset
from trl import DataCollatorForCompletionOnlyLM, SFTConfig, SFTTrainer
from unsloth import FastLanguageModel

logger = logging.getLogger(__name__)

# Gemma chat template marker. Tokens BEFORE this in each tokenized example
# get their loss masked; only the assistant turn contributes to gradients
# (equivalent to MLX-LM's --mask-prompt).
GEMMA_RESPONSE_TEMPLATE = "<start_of_turn>model\n"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--base-model",
        required=True,
        help="HF repo id of the pre-quantized 4-bit base model.",
    )
    p.add_argument("--train-jsonl", type=Path, required=True)
    p.add_argument("--valid-jsonl", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--max-seq-length", type=int, default=4096)
    p.add_argument("--per-device-batch", type=int, default=2)
    p.add_argument(
        "--grad-accum",
        type=int,
        default=4,
        help="Effective batch = per_device_batch * grad_accum.",
    )
    p.add_argument("--lora-rank", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--learning-rate", type=float, default=1e-5)
    p.add_argument("--max-steps", type=int, default=600)
    p.add_argument("--warmup-ratio", type=float, default=0.05)
    p.add_argument("--eval-steps", type=int, default=100)
    p.add_argument("--save-steps", type=int, default=200)
    p.add_argument("--seed", type=int, default=3407)
    p.add_argument(
        "--resume-from-checkpoint",
        type=Path,
        default=None,
        help="Resume from a Trainer checkpoint dir (e.g. .../checkpoint-200).",
    )
    return p


def _load_model(
    base_model: str,
    max_seq_length: int,
    lora_rank: int,
    lora_alpha: int,
    seed: int,
):
    """Load 4-bit base + wrap with LoRA on every linear projection."""
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base_model,
        max_seq_length=max_seq_length,
        dtype=torch.bfloat16,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=lora_rank,
        lora_alpha=lora_alpha,
        lora_dropout=0,
        bias="none",
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        use_gradient_checkpointing="unsloth",
        random_state=seed,
    )
    return model, tokenizer


def _load_datasets(train_jsonl: Path, valid_jsonl: Path, tokenizer):
    """Apply the Gemma chat template once, write to a 'text' column."""
    train = load_dataset("json", data_files=str(train_jsonl), split="train")
    valid = load_dataset("json", data_files=str(valid_jsonl), split="train")

    def format_chat(example: dict) -> dict:
        return {
            "text": tokenizer.apply_chat_template(
                example["messages"],
                tokenize=False,
                add_generation_prompt=False,
            )
        }

    train = train.map(format_chat, remove_columns=["messages"])
    valid = valid.map(format_chat, remove_columns=["messages"])
    return train, valid


def _install_usr1_handler(trainer: SFTTrainer, output_dir: Path) -> None:
    """SIGUSR1 → flush checkpoint and exit cleanly.

    Lets the next job pick up via --resume-from-checkpoint instead of starting
    over when --time runs out. Forwarded by sbatch from the bash-level trap.
    """

    def handler(signum, frame):  # noqa: ARG001
        ckpt = output_dir / "checkpoint-usr1"
        logger.warning("SIGUSR1 received — flushing checkpoint to %s", ckpt)
        trainer.save_model(str(ckpt))
        sys.exit(0)

    signal.signal(signal.SIGUSR1, handler)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _build_parser().parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    model, tokenizer = _load_model(
        args.base_model,
        args.max_seq_length,
        args.lora_rank,
        args.lora_alpha,
        args.seed,
    )
    train_ds, valid_ds = _load_datasets(
        args.train_jsonl, args.valid_jsonl, tokenizer,
    )

    response_template_ids = tokenizer.encode(
        GEMMA_RESPONSE_TEMPLATE, add_special_tokens=False,
    )
    # Guard against the standalone-vs-in-context tokenization mismatch:
    # if the template's tokens don't appear verbatim inside a real example,
    # DataCollatorForCompletionOnlyLM masks the entire sequence to -100 and
    # training silently does nothing.
    sample_ids = tokenizer(
        train_ds[0]["text"], add_special_tokens=False,
    )["input_ids"]
    found = any(
        sample_ids[i : i + len(response_template_ids)] == response_template_ids
        for i in range(len(sample_ids) - len(response_template_ids) + 1)
    )
    if not found:
        raise RuntimeError(
            "Response template tokens not found in a real tokenized chat. "
            "Loss masking would zero out every example. Re-encode the "
            "template with a leading newline and slice, or pass an explicit "
            "instruction_template to the collator."
        )

    collator = DataCollatorForCompletionOnlyLM(
        response_template=response_template_ids,
        tokenizer=tokenizer,
    )

    config = SFTConfig(
        output_dir=str(args.output_dir),
        per_device_train_batch_size=args.per_device_batch,
        gradient_accumulation_steps=args.grad_accum,
        max_steps=args.max_steps,
        learning_rate=args.learning_rate,
        warmup_ratio=args.warmup_ratio,
        lr_scheduler_type="cosine",
        bf16=True,
        tf32=True,
        max_seq_length=args.max_seq_length,
        dataset_text_field="text",
        optim="adamw_8bit",
        logging_steps=10,
        save_strategy="steps",
        save_steps=args.save_steps,
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        save_total_limit=3,
        report_to=[],
        seed=args.seed,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_ds,
        eval_dataset=valid_ds,
        data_collator=collator,
        args=config,
    )

    _install_usr1_handler(trainer, args.output_dir)

    resume = str(args.resume_from_checkpoint) if args.resume_from_checkpoint else None
    trainer.train(resume_from_checkpoint=resume)
    trainer.save_model(str(args.output_dir / "final"))
    logger.info("Done. Final adapter at %s", args.output_dir / "final")


if __name__ == "__main__":
    main()
