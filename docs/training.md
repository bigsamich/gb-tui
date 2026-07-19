# Training a local gb-copilot model

This is the follow-up recipe for turning collected gameplay journals into a
fine-tuned local model served by Ollama. Everything runs on the local machine.

## 1. Collect data

Data accumulates automatically in `journal/<session>/events.jsonl` whenever you:

- play in the TUI (human inputs are logged with game state),
- ask the copilot questions (`?` — question/answer pairs with state),
- run the autopilot (`Tab` — goal/action/outcome decisions),
- run `gb-agent … --journal journal/` scripts.

## 2. Export datasets

```sh
# Advice model data (ShareGPT chat format):
./target/release/gb-agent export --journal journal/<session> --format advice --out advice.jsonl

# Policy model data (state -> action completion pairs):
./target/release/gb-agent export --journal journal/<session> --format policy --out policy.jsonl
```

Concatenate exports from multiple sessions with `cat`.

## 3. Fine-tune (LoRA) on the GB10

Use LLaMA-Factory (or unsloth) with the HF equivalent of your Ollama base model
(e.g. `Qwen/Qwen2.5-14B-Instruct` for `qwen2.5:14b`):

```sh
pip install llamafactory
llamafactory-cli train \
  --model_name_or_path Qwen/Qwen2.5-14B-Instruct \
  --dataset advice --dataset_dir . \
  --template qwen --finetuning_type lora \
  --output_dir gb-copilot-lora --num_train_epochs 3 \
  --per_device_train_batch_size 2 --learning_rate 1e-4
```

(Register `advice.jsonl` in LLaMA-Factory's `dataset_info.json` as a sharegpt
dataset. Start with the advice dataset; the policy dataset needs a few thousand
examples before it is worth training.)

## 4. Serve with Ollama

```
# Modelfile
FROM qwen2.5:14b
ADAPTER ./gb-copilot-lora
```

```sh
ollama create gb-copilot -f Modelfile
```

## 5. Point the game at it

```toml
# gb-tui.toml
model = "gb-copilot"
```
