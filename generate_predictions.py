"""
Generate Druid SQL predictions for an eval split, for scoring with score_predictions.py.

One JSONL line is written per eval row:
  {"id", "question", "prediction", "raw_output", "gold", "prompt_tokens",
   "generated_tokens", "hit_max_new_tokens", "seconds"}

Examples:
  # fine-tuned (final adapter)
  python generate_predictions.py --adapter /path/to/qwen_3_5_2B_lora_v2 --tag final
  # a checkpoint
  python generate_predictions.py --adapter /path/to/qwen_3_5_2B_lora_v2/checkpoint-500 --tag ckpt500
  # base model, no adapter
  python generate_predictions.py --adapter none --tag base

The script resumes automatically: rows already present in the output file are skipped,
so you can Ctrl+C and rerun the same command.
"""
import argparse
import json
import os
import re
import time

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

BASE_MODEL = "Qwen/Qwen3.5-2B"
DATASET_DIR = "/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/druid_sql_query_llm_finetuning/dataset/v2"
DEFAULT_SPLITS = ["test_heldout_shape", "test_prodlike"]
DEFAULT_OUT_DIR = "/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/druid_sql_query_llm_finetuning/predictions"

# If score_predictions.py expects different key names, change them here.
ID_KEY = "id"
PREDICTION_KEY = "prediction"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--adapter", required=True,
                   help="Path to a LoRA adapter or checkpoint folder, or 'none' for the base model.")
    p.add_argument("--tag", required=True, help="Name for this model run, e.g. final, ckpt500, base.")
    p.add_argument("--splits", nargs="+", default=DEFAULT_SPLITS)
    p.add_argument("--dataset-dir", default=DATASET_DIR)
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    p.add_argument("--max-new-tokens", type=int, default=1024)
    p.add_argument("--limit", type=int, default=None, help="Only run the first N rows of each split (for a quick test).")
    p.add_argument("--no-merge", action="store_true",
                   help="Keep the adapter separate instead of merging it into the base weights.")
    return p.parse_args()


def load_model(adapter: str, merge: bool):
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, dtype=torch.bfloat16)
    if adapter.lower() != "none":
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter)
        if merge:
            # Merging gives the same outputs as the unmerged adapter, without the extra
            # LoRA matmuls on every forward pass. Only the in-memory copy is changed.
            model = model.merge_and_unload()
    model.to("cuda" if torch.cuda.is_available() else "cpu")
    model.eval()
    return model, tokenizer


def stop_token_ids(tokenizer):
    ids = set()
    if tokenizer.eos_token_id is not None:
        ids.add(tokenizer.eos_token_id)
    im_end = tokenizer.convert_tokens_to_ids("<|im_end|>")
    if isinstance(im_end, int) and im_end != tokenizer.unk_token_id:
        ids.add(im_end)
    return sorted(ids)


def clean_output(text: str) -> str:
    """Strip leftovers that are not part of the answer (empty think block, markdown fence)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fence = re.fullmatch(r"```(?:sql)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    return text


def done_ids(path: str) -> set:
    if not os.path.exists(path):
        return set()
    ids = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                ids.add(json.loads(line)[ID_KEY])
    return ids


@torch.no_grad()
def run_split(model, tokenizer, split: str, args, eos_ids):
    in_path = os.path.join(args.dataset_dir, f"{split}.jsonl")
    out_dir = os.path.join(args.out_dir, args.tag)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{split}.jsonl")

    with open(in_path) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    if args.limit:
        rows = rows[: args.limit]

    skip = done_ids(out_path)
    todo = [r for r in rows if r["meta"]["id"] not in skip]
    print(f"[{args.tag}] {split}: {len(rows)} rows, {len(skip)} already done, {len(todo)} to run -> {out_path}")

    with open(out_path, "a") as out:
        for i, row in enumerate(todo, 1):
            messages = row["messages"][:-1]  # drop the gold assistant turn
            gold = row["messages"][-1]["content"]

            inputs = tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                enable_thinking=False,
                return_tensors="pt",
                return_dict=True,
            ).to(model.device)
            prompt_len = inputs["input_ids"].shape[1]

            t0 = time.time()
            output = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,              # greedy: reproducible, comparable across models
                eos_token_id=eos_ids,
                pad_token_id=tokenizer.pad_token_id or eos_ids[0],
            )
            seconds = time.time() - t0

            new_tokens = output[0][prompt_len:]
            raw = tokenizer.decode(new_tokens, skip_special_tokens=True)
            n_new = int(new_tokens.shape[0])

            record = {
                ID_KEY: row["meta"]["id"],
                "question": row["meta"].get("question"),
                PREDICTION_KEY: clean_output(raw),
                "raw_output": raw,
                "gold": gold,
                "prompt_tokens": int(prompt_len),
                "generated_tokens": n_new,
                "hit_max_new_tokens": n_new >= args.max_new_tokens,
                "seconds": round(seconds, 2),
            }
            out.write(json.dumps(record) + "\n")
            out.flush()  # keep progress if interrupted

            print(f"  {i}/{len(todo)} {record[ID_KEY]}  prompt={prompt_len} gen={n_new} {seconds:.1f}s"
                  + ("  [HIT MAX TOKENS]" if record["hit_max_new_tokens"] else ""))


def main():
    args = parse_args()
    model, tokenizer = load_model(args.adapter, merge=not args.no_merge)
    eos_ids = stop_token_ids(tokenizer)
    print(f"Model: {BASE_MODEL} + adapter={args.adapter}  stop tokens={eos_ids}")
    for split in args.splits:
        run_split(model, tokenizer, split, args, eos_ids)


if __name__ == "__main__":
    main()