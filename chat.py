"""
chat.py — Chat directo con MyAI desde la terminal
Sin servidor, sin API — carga el modelo y chateás directamente

Usar con: python chat.py
"""
import torch, pathlib, sys

MERGED_DIR = "finetune/merged"
LORA_DIR   = "finetune/output"
SYSTEM_MSG = (
    "Sos MyAI, un asistente experto en desarrollo web (HTML, CSS, JavaScript, "
    "React, Python, TypeScript), diseño UX/UI, product design y copywriting/"
    "marketing digital. Respondés siempre en español con respuestas precisas, "
    "profesionales y directamente aplicables."
)

print("=" * 55)
print("  MyAI — Chat local")
print("=" * 55)

# Cargar modelo
print("Cargando modelo...")
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

use_4bit = True  # Cambiar a False si querés más calidad y tenés VRAM

if pathlib.Path(MERGED_DIR).exists():
    model_path = MERGED_DIR
    print(f"  Usando modelo merged: {MERGED_DIR}")
    if use_4bit:
        bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16)
        model = AutoModelForCausalLM.from_pretrained(
            model_path, quantization_config=bnb, device_map="auto", trust_remote_code=True
        )
    else:
        model = AutoModelForCausalLM.from_pretrained(
            model_path, device_map="auto", torch_dtype=torch.bfloat16, trust_remote_code=True
        )
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

elif pathlib.Path(LORA_DIR).exists():
    print(f"  Usando adapters LoRA: {LORA_DIR}")
    from peft import PeftModel
    base_name = "Qwen/Qwen2.5-7B-Instruct"
    bnb = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16
    )
    tokenizer = AutoTokenizer.from_pretrained(LORA_DIR, trust_remote_code=True)
    base = AutoModelForCausalLM.from_pretrained(
        base_name, quantization_config=bnb, device_map="auto", trust_remote_code=True
    )
    model = PeftModel.from_pretrained(base, LORA_DIR)
else:
    print("ERROR: No encontre el modelo.")
    print("Ejecuta primero: python finetune\\export_model.py")
    sys.exit(1)

model.eval()
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

print("  Modelo listo")
if torch.cuda.is_available():
    used  = torch.cuda.memory_allocated() / 1e9
    total = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f"  VRAM: {used:.1f} / {total:.1f} GB")

print("\n  Comandos: 'salir' para terminar, 'limpiar' para nueva conversacion")
print("=" * 55 + "\n")

# Chat loop
history = []

def respond(user_input: str) -> str:
    history.append({"role": "user", "content": user_input})
    chat = [{"role": "system", "content": SYSTEM_MSG}] + history

    text = tokenizer.apply_chat_template(
        chat, tokenize=False, add_generation_prompt=True
    )
    inputs = tokenizer(text, return_tensors="pt", truncation=True,
                       max_length=3072).to(model.device)

    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=600,
            temperature=0.7,
            top_p=0.9,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id,
            repetition_penalty=1.1,
        )

    response = tokenizer.decode(
        out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
    ).strip()
    history.append({"role": "assistant", "content": response})
    return response

while True:
    try:
        user = input("Vos: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nHasta luego.")
        break

    if not user:
        continue
    if user.lower() == "salir":
        print("Hasta luego.")
        break
    if user.lower() == "limpiar":
        history.clear()
        print("  Conversacion limpiada.\n")
        continue

    print("\nMyAI: ", end="", flush=True)
    response = respond(user)
    print(response)
    print()
