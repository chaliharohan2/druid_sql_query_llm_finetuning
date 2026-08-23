"""
Important docs I used for this:
- huggingface.co/docs/trl/main/en/peft_integration
- huggingface.co/docs/trl/sft_trainer
- huggingface.co/docs/peft/developer_guides/lora
- huggingface.co/docs/peft/en/developer_guides/quantization
"""
from peft import LoraConfig, get_peft_model, TaskType
from transformers import AutoModelForCausalLM, AutoTokenizer
from datasets import load_dataset
from trl import SFTConfig, SFTTrainer
import json

if __name__ == "__main__":

    # load the dataset
    dataset = load_dataset("json", data_files="/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/druid_sql_query_llm_finetuning/dataset/batch01.sft.jsonl", split="train")
    # Load base model
    model = AutoModelForCausalLM.from_pretrained("Qwen/Qwen3.5-2B")
    tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-2B")
    
    # Apply parameter efficient fine-tuning config
    peft_config = LoraConfig(
        r=32,
        lora_alpha=16,
        lora_dropout=0.05,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
        target_modules=[
            "in_proj_qkv", "in_proj_z", "out_proj",
            "gate_proj", "up_proj", "down_proj",
            "q_proj", "k_proj", "v_proj", "o_proj"
        ]
    )

    model = get_peft_model(model=model, peft_config=peft_config)

    training_args = SFTConfig(
        learning_rate=2.0e-4,
        assistant_only_loss=True,
        max_length=2048
    )
    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        processing_class=tokenizer
    )

    trainer.train()

    trainer.save_model("/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/druid_sql_query_llm_finetuning/models/qwen_3_5_2B_lora")