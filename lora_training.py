"""
Important docs I used for this:
- huggingface.co/docs/trl/main/en/peft_integration
- huggingface.co/docs/trl/sft_trainer
- huggingface.co/docs/peft/developer_guides/lora
- huggingface.co/docs/peft/en/developer_guides/quantization
"""
from peft import LoraConfig, get_peft_model, TaskType
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback
from datasets import load_dataset
from trl import SFTConfig, SFTTrainer
import torch

DEVICE = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")

class InferenceCallback(TrainerCallback):
    def __init__(self, tokenizer: AutoTokenizer, test_messages, n_steps, max_tokens=2048):
        self.tokenizer = tokenizer
        self.test_messages = test_messages
        self.n_steps = n_steps
        self.max_tokens = max_tokens

    def on_step_end(self, args, state, control, model: AutoModelForCausalLM = None, **kwargs):
        if state.global_step == 0 or state.global_step % self.n_steps != 0:
            return control

        model.eval()
        was_caching = model.config.use_cache
        model.config.use_cache = True

        print("\n--------------- Model Inference ------------------\n")
        with torch.no_grad():
            for msg in self.test_messages:
                messages = msg["messages"][:-1] # dropping assitant response
                tokenized_chat = self.tokenizer.apply_chat_template(
                                    messages, 
                                    tokenize=True, 
                                    add_generation_prompt=True, 
                                    enable_thinking=False, 
                                    return_tensors="pt",
                                    return_dict=True
                                ).to(DEVICE)
                outputs = model.generate(**tokenized_chat , max_new_tokens=self.max_tokens)
                print(self.tokenizer.decode(outputs[0], skip_special_tokens=True))
        print("\n------------------ End Inference ------------------\n")

        model.config.use_cache = was_caching
        model.train()
        return control

if __name__ == "__main__":

    # load the dataset
    dataset = load_dataset("json", data_files="/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/druid_sql_query_llm_finetuning/dataset/batch01.sft.jsonl", split="train")
    # Load base model
    model = AutoModelForCausalLM.from_pretrained("Qwen/Qwen3.5-2B", dtype=torch.bfloat16)

    tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-2B")
    
    # Apply parameter efficient fine-tuning config
    peft_config = LoraConfig(
        r=32,
        lora_alpha=64,
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
    model.to(DEVICE) # move model to GPU

    training_args = SFTConfig(
        learning_rate=2.0e-4,
        assistant_only_loss=True,
        per_device_train_batch_size=2,
        num_train_epochs=3,
        max_length=2048,
        logging_steps=10,
        output_dir="/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/druid_sql_query_llm_finetuning/models/qwen_3_5_2B_lora",
        lr_scheduler_type="cosine",
        warmup_steps=2,
        # warmup_ratio=0.03
    )
    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        processing_class=tokenizer,
        callbacks=[InferenceCallback(tokenizer=tokenizer, test_messages=[dataset[0], dataset[2]], n_steps=10)]
    )

    trainer.train()

    trainer.save_model("/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/druid_sql_query_llm_finetuning/models/qwen_3_5_2B_lora")