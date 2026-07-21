"""LoRA fine-tune (bf16, no bitsandbytes) of Qwen3 on the pokered dataset.

Deliberately plain HF transformers + PEFT so it works on new silicon (GB10):
no 4-bit quant, no flash-attn requirement (sdpa), no packing tricks.

Usage:
  python3 train_lora.py --data data/v1 --model Qwen/Qwen3-8B --out runs/pokered-8b-v1
  python3 train_lora.py --smoke   # tiny model + 64 examples, verifies the stack
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model
from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer,
                          TrainingArguments)

# --data resolves under the active game's data dir; --out (trained models) lives
# under training/runs (gitignored) as before the restructure.
DATA_ROOT = _bootstrap.GAME_DIR
OUT_ROOT = _bootstrap.REPO_ROOT / "training"


def load_split(path: Path):
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    return Dataset.from_list([{"messages": r["messages"]} for r in rows])


def build_tokenize_fn(tok, max_len):
    def fn(batch):
        input_ids, labels = [], []
        for msgs in batch["messages"]:
            # render to text first (transformers 5.x returns Encoding from tokenize=True)
            full_text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
            prompt_text = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)
            full = tok(full_text, add_special_tokens=False)["input_ids"]
            prompt = tok(prompt_text, add_special_tokens=False)["input_ids"]
            ids = full[:max_len]
            lab = list(ids)
            for i in range(min(len(prompt), len(lab))):
                lab[i] = -100
            input_ids.append(ids)
            labels.append(lab)
        return {"input_ids": input_ids, "labels": labels}
    return fn


def collate(tok):
    def fn(features):
        maxlen = max(len(f["input_ids"]) for f in features)
        pad = tok.pad_token_id or tok.eos_token_id
        batch_ids, batch_lab, batch_att = [], [], []
        for f in features:
            ids, lab = f["input_ids"], f["labels"]
            d = maxlen - len(ids)
            batch_ids.append(ids + [pad] * d)
            batch_lab.append(lab + [-100] * d)
            batch_att.append([1] * len(ids) + [0] * d)
        return {"input_ids": torch.tensor(batch_ids),
                "labels": torch.tensor(batch_lab),
                "attention_mask": torch.tensor(batch_att)}
    return fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/v1")
    ap.add_argument("--model", default="Qwen/Qwen3-8B")
    ap.add_argument("--out", default="runs/pokered-8b-v1")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=2048)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--accum", type=int, default=16)
    ap.add_argument("--smoke", action="store_true",
                    help="tiny run: Qwen3-0.6B, 64 examples, 8 steps")
    args = ap.parse_args()

    if args.smoke:
        args.model, args.out = "Qwen/Qwen3-0.6B", "runs/smoke"
        args.batch, args.accum, args.max_len = 2, 1, 1024

    data_dir = (DATA_ROOT / args.data) if not Path(args.data).is_absolute() else Path(args.data)
    train_ds, val_ds = load_split(data_dir / "train.jsonl"), load_split(data_dir / "val.jsonl")
    if args.smoke:
        train_ds, val_ds = train_ds.select(range(64)), val_ds.select(range(16))

    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(
        args.model, dtype=torch.bfloat16, attn_implementation="sdpa", device_map="cuda")
    model.config.use_cache = False

    lora = LoraConfig(
        r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    mapper = build_tokenize_fn(tok, args.max_len)
    train_tok = train_ds.map(mapper, batched=True, remove_columns=["messages"])
    val_tok = val_ds.map(mapper, batched=True, remove_columns=["messages"])

    targs = TrainingArguments(
        output_dir=str(OUT_ROOT / args.out),
        num_train_epochs=args.epochs,
        max_steps=8 if args.smoke else -1,
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.accum,
        learning_rate=args.lr, lr_scheduler_type="cosine", warmup_ratio=0.03,
        bf16=True, logging_steps=5,
        eval_strategy="steps", eval_steps=100 if not args.smoke else 8,
        save_strategy="epoch" if not args.smoke else "no",
        gradient_checkpointing=True, report_to=[],
    )
    trainer = Trainer(model=model, args=targs, train_dataset=train_tok,
                      eval_dataset=val_tok, data_collator=collate(tok))
    trainer.train()
    trainer.save_model(str(OUT_ROOT / args.out / "adapter"))
    tok.save_pretrained(str(OUT_ROOT / args.out / "adapter"))
    print("saved adapter ->", OUT_ROOT / args.out / "adapter")


if __name__ == "__main__":
    main()
