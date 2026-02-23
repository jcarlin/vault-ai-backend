"""Standalone training worker — runs in the training-venv as a subprocess.

Usage:
    python -m app.services.training.worker --config /path/to/config.json

The worker:
1. Loads the base model + dataset
2. Runs SFTTrainer (trl/peft) for LoRA/QLoRA fine-tuning
3. Writes status.json every N steps with progress metrics
4. Saves the adapter to the output_dir on completion
5. Handles SIGTERM (cancel) and SIGUSR1 (pause → checkpoint and exit 42)

Exit codes:
    0  = success
    42 = paused (checkpoint saved, can resume)
    1  = error
"""

import argparse
import json
import signal
import sys
import time
import uuid
from pathlib import Path

# ── Signal handling ──────────────────────────────────────────────────────────

_cancel_requested = False
_pause_requested = False


def _handle_sigterm(signum, frame):
    global _cancel_requested
    _cancel_requested = True


def _handle_sigusr1(signum, frame):
    global _pause_requested
    _pause_requested = True


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGUSR1, _handle_sigusr1)


def _write_status(status_dir: str, data: dict) -> None:
    """Write status.json atomically."""
    status_path = Path(status_dir) / "status.json"
    tmp_path = status_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data))
    tmp_path.rename(status_path)


def run_training(config_path: str) -> None:
    """Main training loop."""
    config = json.loads(Path(config_path).read_text())

    job_id = config["job_id"]
    base_model_path = config["base_model_path"]
    dataset_path = config["dataset_path"]
    output_dir = config["output_dir"]
    status_dir = config["status_dir"]

    # LoRA params
    adapter_type = config.get("adapter_type", "lora")
    lora_rank = config.get("lora_rank", 16)
    lora_alpha = config.get("lora_alpha", 32)
    lora_dropout = config.get("lora_dropout", 0.05)
    target_modules = config.get("lora_target_modules", ["q_proj", "v_proj"])
    quantization_bits = config.get("quantization_bits")

    # Training params
    epochs = config.get("epochs", 10)
    batch_size = config.get("batch_size", 32)
    learning_rate = config.get("learning_rate", 1e-4)
    warmup_steps = config.get("warmup_steps", 100)
    weight_decay = config.get("weight_decay", 0.01)
    log_steps = config.get("log_steps", 10)
    max_memory_pct = config.get("max_memory_pct", 0.9)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(status_dir).mkdir(parents=True, exist_ok=True)

    _write_status(status_dir, {"state": "loading", "step": 0, "total_steps": 0})

    try:
        # Import training libraries (only available in training-venv)
        import torch
        from datasets import load_dataset
        from peft import LoraConfig, TaskType, get_peft_model
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            TrainingArguments,
        )
        from trl import SFTTrainer

        # Load tokenizer
        tokenizer = AutoTokenizer.from_pretrained(base_model_path, trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        # Load model (with optional quantization for QLoRA)
        model_kwargs = {
            "trust_remote_code": True,
            "torch_dtype": torch.float16,
            "max_memory": {0: f"{int(max_memory_pct * 100)}%"},
        }

        if quantization_bits == 4:
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
            )
        elif quantization_bits == 8:
            model_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)

        model = AutoModelForCausalLM.from_pretrained(base_model_path, **model_kwargs)

        # Configure LoRA
        peft_config = LoraConfig(
            r=lora_rank,
            lora_alpha=lora_alpha,
            lora_dropout=lora_dropout,
            target_modules=target_modules,
            task_type=TaskType.CAUSAL_LM,
            bias="none",
        )

        model = get_peft_model(model, peft_config)

        # Load dataset
        dataset = load_dataset("json", data_files=dataset_path, split="train")

        # Calculate total steps
        total_steps = (len(dataset) // batch_size) * epochs
        if total_steps == 0:
            total_steps = epochs

        _write_status(status_dir, {
            "state": "training",
            "step": 0,
            "total_steps": total_steps,
            "total_epochs": epochs,
        })

        # Training arguments
        training_args = TrainingArguments(
            output_dir=output_dir,
            num_train_epochs=epochs,
            per_device_train_batch_size=batch_size,
            learning_rate=learning_rate,
            warmup_steps=warmup_steps,
            weight_decay=weight_decay,
            logging_steps=log_steps,
            save_strategy="epoch",
            fp16=True,
            report_to="none",
            remove_unused_columns=False,
        )

        # Status callback
        loss_history = []

        class StatusCallback:
            def on_log(self, args, state, control, logs=None, **kwargs):
                if _cancel_requested or _pause_requested:
                    control.should_training_stop = True
                    return

                step = state.global_step
                loss = logs.get("loss") if logs else None
                lr = logs.get("learning_rate") if logs else None

                if loss is not None:
                    loss_history.append({"step": step, "loss": loss})

                eta = None
                if step > 0:
                    elapsed = time.time() - start_time
                    remaining_steps = total_steps - step
                    eta = int(elapsed / step * remaining_steps)

                _write_status(status_dir, {
                    "state": "training",
                    "step": step,
                    "total_steps": total_steps,
                    "epoch": state.epoch,
                    "total_epochs": epochs,
                    "loss": loss,
                    "lr": lr,
                    "tokens_processed": step * batch_size,
                    "eta_seconds": eta,
                    "loss_history": loss_history[-100:],
                })

            def on_step_end(self, args, state, control, **kwargs):
                if _cancel_requested or _pause_requested:
                    control.should_training_stop = True

        start_time = time.time()

        # Run training
        trainer = SFTTrainer(
            model=model,
            args=training_args,
            train_dataset=dataset,
            tokenizer=tokenizer,
            peft_config=peft_config,
            callbacks=[StatusCallback()],
        )

        trainer.train()

        # Handle interruption
        if _cancel_requested:
            _write_status(status_dir, {"state": "cancelled"})
            sys.exit(143)

        if _pause_requested:
            # Save checkpoint for resume
            trainer.save_model(output_dir)
            _write_status(status_dir, {
                "state": "paused",
                "step": trainer.state.global_step,
                "total_steps": total_steps,
                "checkpoint_path": output_dir,
            })
            sys.exit(42)

        # Save adapter
        adapter_id = str(uuid.uuid4())
        trainer.save_model(output_dir)
        tokenizer.save_pretrained(output_dir)

        # Write adapter metadata
        metadata = {
            "adapter_id": adapter_id,
            "job_id": job_id,
            "base_model": base_model_path,
            "adapter_type": adapter_type,
            "config": {
                "rank": lora_rank,
                "alpha": lora_alpha,
                "dropout": lora_dropout,
                "target_modules": target_modules,
                "quantization_bits": quantization_bits,
            },
        }
        (Path(output_dir) / "metadata.json").write_text(json.dumps(metadata, indent=2))

        # Final metrics
        final_metrics = {
            "loss": loss_history[-1]["loss"] if loss_history else None,
            "epochs_completed": epochs,
            "total_epochs": epochs,
            "steps_completed": total_steps,
            "total_steps": total_steps,
            "loss_history": loss_history[-100:],
        }

        _write_status(status_dir, {
            "state": "completed",
            "adapter_id": adapter_id,
            "metrics": final_metrics,
            "step": total_steps,
            "total_steps": total_steps,
        })

        sys.exit(0)

    except Exception as e:
        error_msg = str(e)
        _write_status(status_dir, {
            "state": "failed",
            "error": error_msg[:2000],
        })
        print(f"Training failed: {error_msg}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vault AI Training Worker")
    parser.add_argument("--config", required=True, help="Path to training run config JSON")
    args = parser.parse_args()
    run_training(args.config)
