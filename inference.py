from peft import LoraConfig, get_peft_model, TaskType, PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, TextStreamer
import json
import sys
import torch

device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
stream = True
use_custom_question = True
BASE_MODEL = "Qwen/Qwen3.5-9B"
LORA_ADAPTER_MODEL = "/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/druid_sql_query_llm_finetuning/models/qwen_3_5_2B_lora/checkpoint-1500"

model = AutoModelForCausalLM.from_pretrained(BASE_MODEL)
# peft_model = PeftModel.from_pretrained(model, LORA_ADAPTER_MODEL)
# model = peft_model.merge_and_unload()
tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)

if __name__ == "__main__":

    while True:

        if not use_custom_question:
            dataset = []
            try:
                with open("/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/druid_sql_query_llm_finetuning/dataset/val.jsonl", mode="r") as f:
                    for line in f:
                        data_entry = json.loads(s=line)
                        dataset.append(data_entry)
            except Exception as e:
                print(str(e))

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

        else:

            with open("/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/druid_sql_query_llm_finetuning/del_leg_wh_prompt_md_format.md", mode="r") as file:
                sys_prompt = file.read()

            user_input = input("> ").strip()
            if user_input == "q":
                print("Exiting....")
                sys.exit(0)
            elif not user_input:
                continue
            
            messages = [
                {
                    "role": "system",
                    "content": sys_prompt
                },
                {
                    "role": "user",
                    "content": user_input
                }
            ]
        

        tokenized_chat = tokenizer.apply_chat_template(
            messages, 
            tokenize=True, 
            add_generation_prompt=True, 
            enable_thinking=False, 
            return_tensors="pt",
            return_dict=True
            ).to(device)
        model.to(device)

        if stream:
            # streaming approach
            streamer = TextStreamer(tokenizer=tokenizer, skip_prompt=False, skip_special_tokens=True)
            outputs = model.generate(**tokenized_chat , max_new_tokens=2048, streamer=streamer)
        else:
            # direct approach
            outputs = model.generate(**tokenized_chat , max_new_tokens=2048)
            print(tokenizer.decode(outputs[0], skip_special_tokens=True))