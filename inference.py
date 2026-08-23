from peft import LoraConfig, get_peft_model, TaskType, PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
import json
import sys
import torch

device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")

model = AutoModelForCausalLM.from_pretrained("Qwen/Qwen3.5-2B")
peft_model = PeftModel.from_pretrained(model, "/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/druid_sql_query_llm_finetuning/models/qwen_3_5_2B_lora")
model = peft_model.merge_and_unload()
tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-2B")

dataset = []

try:
    with open("/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/druid_sql_query_llm_finetuning/dataset/batch01.sft.jsonl", mode="r") as f:
        for line in f:
            data_entry = json.loads(s=line)
            dataset.append(data_entry)
except Exception as e:
    print(str(e))

if __name__ == "__main__":

    while True:
        user_input = input("Index to check: ").strip()
        if user_input == "q":
            print("Exiting....")
            sys.exit(0)
        elif not user_input:
            continue
        else:
            idx = int(user_input) 

        messages: list = list(dataset[idx]["messages"])
        # print(messages)
        messages.pop(-1)

        tokenized_chat = tokenizer.apply_chat_template(
            messages, 
            tokenize=True, 
            add_generation_prompt=True, 
            enable_thinking=False, 
            return_tensors="pt",
            return_dict=True
            ).to(device)
        model.to(device)
        outputs = model.generate(**tokenized_chat , max_new_tokens=200)
        print(tokenizer.decode(outputs[0], skip_special_tokens=True))