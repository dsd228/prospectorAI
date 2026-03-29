"""
api.py — API REST para MyAI, compatible con formato OpenAI
Usar con: python api.py
Puerto: http://localhost:8000

Endpoints:
  POST /chat          — conversacion con historial
  POST /v1/chat/completions — formato OpenAI (compatible con Open WebUI)
  GET  /health        — status
  GET  /              — info
"""
import json, pathlib, time, uuid, torch
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# ── Config ────────────────────────────────────────────────────────
MERGED_DIR  = "finetune/merged"
MAX_TOKENS  = 2048
DEVICE      = "cuda" if torch.cuda.is_available() else "cpu"
SYSTEM_MSG  = (
    "Sos MyAI, un asistente experto en desarrollo web (HTML, CSS, JavaScript, "
    "React, Python, TypeScript), diseño UX/UI, product design y copywriting/"
    "marketing digital. Respondés siempre en español con respuestas precisas, "
    "profesionales y directamente aplicables. Cuando escribís código, usás "
    "buenas prácticas de producción."
)

# ── Modelos Pydantic ──────────────────────────────────────────────
class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 500
    top_p: Optional[float] = 0.9
    stream: Optional[bool] = False

class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str = "myai"
    choices: List[dict]
    usage: dict

# ── App ───────────────────────────────────────────────────────────
app = FastAPI(title="MyAI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Estado global del modelo ──────────────────────────────────────
model_state = {"model": None, "tokenizer": None, "loaded": False, "error": None}

def load_model():
    global model_state
    try:
        print(f"Cargando modelo desde {MERGED_DIR}...")
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if not pathlib.Path(MERGED_DIR).exists():
            # Intentar cargar desde los adapters LoRA directamente
            lora_dir = "finetune/output"
            if pathlib.Path(lora_dir).exists():
                print(f"  Merged no encontrado, cargando desde adapters: {lora_dir}")
                from transformers import BitsAndBytesConfig
                from peft import PeftModel

                bnb = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                )
                base_model_name = "Qwen/Qwen2.5-7B-Instruct"
                tokenizer = AutoTokenizer.from_pretrained(lora_dir, trust_remote_code=True)
                base = AutoModelForCausalLM.from_pretrained(
                    base_model_name, quantization_config=bnb,
                    device_map="auto", trust_remote_code=True,
                )
                model = PeftModel.from_pretrained(base, lora_dir)
            else:
                raise FileNotFoundError(
                    f"No encontre modelo en {MERGED_DIR} ni en {lora_dir}.\n"
                    "Ejecuta primero: python finetune\\export_model.py"
                )
        else:
            tokenizer = AutoTokenizer.from_pretrained(MERGED_DIR, trust_remote_code=True)
            model = AutoModelForCausalLM.from_pretrained(
                MERGED_DIR,
                device_map="auto",
                torch_dtype=torch.bfloat16,
                trust_remote_code=True,
            )

        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        model.eval()
        model_state["model"] = model
        model_state["tokenizer"] = tokenizer
        model_state["loaded"] = True
        print(f"  Modelo listo en {DEVICE}")
        if torch.cuda.is_available():
            used = torch.cuda.memory_allocated() / 1e9
            total = torch.cuda.get_device_properties(0).total_memory / 1e9
            print(f"  VRAM: {used:.1f} / {total:.1f} GB")

    except Exception as e:
        model_state["error"] = str(e)
        print(f"ERROR cargando modelo: {e}")

# ── Generacion ────────────────────────────────────────────────────
def generate_response(messages: List[Message], temperature: float,
                      max_tokens: int, top_p: float) -> str:
    if not model_state["loaded"]:
        raise HTTPException(status_code=503, detail="Modelo no cargado aun")

    model = model_state["model"]
    tokenizer = model_state["tokenizer"]

    # Construir historial con system prompt
    chat = [{"role": "system", "content": SYSTEM_MSG}]
    for m in messages:
        chat.append({"role": m.role, "content": m.content})

    # Aplicar chat template
    text = tokenizer.apply_chat_template(
        chat, tokenize=False, add_generation_prompt=True
    )
    inputs = tokenizer(text, return_tensors="pt", truncation=True,
                       max_length=MAX_TOKENS).to(DEVICE)

    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=max_tokens,
            temperature=max(temperature, 0.01),
            top_p=top_p,
            do_sample=temperature > 0.01,
            pad_token_id=tokenizer.eos_token_id,
            repetition_penalty=1.1,
        )

    response = tokenizer.decode(
        output[0][inputs["input_ids"].shape[1]:],
        skip_special_tokens=True
    )
    return response.strip()

# ── Endpoints ─────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "name": "MyAI API",
        "version": "1.0.0",
        "model": "Qwen2.5-7B fine-tuned",
        "status": "ready" if model_state["loaded"] else "loading",
        "device": DEVICE,
        "docs": "http://localhost:8000/docs",
    }

@app.get("/health")
def health():
    if model_state["error"]:
        return {"status": "error", "detail": model_state["error"]}
    if not model_state["loaded"]:
        return {"status": "loading"}
    vram = {}
    if torch.cuda.is_available():
        vram = {
            "used_gb": round(torch.cuda.memory_allocated() / 1e9, 2),
            "total_gb": round(torch.cuda.get_device_properties(0).total_memory / 1e9, 2),
        }
    return {"status": "ok", "model": "myai", "device": DEVICE, "vram": vram}

@app.post("/chat")
def chat(req: ChatRequest):
    response = generate_response(
        req.messages, req.temperature, req.max_tokens, req.top_p
    )
    return {
        "response": response,
        "model": "myai",
        "usage": {"prompt_tokens": 0, "completion_tokens": len(response.split())},
    }

# Formato OpenAI — compatible con Open WebUI, Continue.dev, etc.
@app.post("/v1/chat/completions")
def openai_chat(req: ChatRequest):
    response = generate_response(
        req.messages, req.temperature, req.max_tokens, req.top_p
    )
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "myai",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": response},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(response.split()),
            "total_tokens": len(response.split()),
        },
    }

# ── Main ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import threading
    print("=" * 55)
    print("  MyAI API — Iniciando servidor")
    print("=" * 55)
    print(f"  URL:  http://localhost:8000")
    print(f"  Docs: http://localhost:8000/docs")
    print(f"  Para Open WebUI: http://localhost:8000/v1")
    print("=" * 55)

    # Cargar modelo en background para no bloquear el servidor
    threading.Thread(target=load_model, daemon=True).start()

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
