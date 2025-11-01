import json
from typing import Optional, Dict, Any
from fastapi import FastAPI
from pydantic import BaseModel
from prompt_builder import compose_prompt

app = FastAPI()

# Load Prompt Builder spec once
with open("promptBuilderSpec.json", "r") as f:
    PROMPT_SPEC = json.load(f)

class GenReq(BaseModel):
    prompt: Optional[str] = None
    builder: Optional[str] = None
    params: Dict[str, Any] = {}

@app.get("/health")
def health():
    return {"ok": True, "service": "nano-banana-bridge", "builder": bool(PROMPT_SPEC)}

@app.post("/generate")
def generate(req: GenReq):
    """
    Accepts EITHER:
      { "prompt": "freeform…" }
    OR:
      { "builder": "product_angle_synthesis", "params": { ... } }
    Renders final prompt and (TODO) calls Gemini, uploads to Cloudinary, returns {url, meta}.
    """
    # 1) Resolve prompt
    if req.prompt:
        final_prompt = req.prompt
        meta = {}
    elif req.builder == "product_angle_synthesis":
        built = compose_prompt(PROMPT_SPEC, req.params or {})
        final_prompt = built["prompt"]
        meta = built["meta"]
    else:
        return {"error": "invalid_input", "details": "Provide `prompt` or `builder`='product_angle_synthesis'."}

    # 2) TODO: Call Gemini image API with final_prompt (+ aspect ratio from req.params.get('ar'))
    # image_data_uri = call_gemini(final_prompt, req.params)

    # 3) TODO: Upload to Cloudinary → get https URL
    # url = upload_to_cloudinary(image_data_uri)

    # Stub (works without keys so we can test I/O and prompt rendering)
    url = "https://res.cloudinary.com/demo/image/upload/example.jpg"

    return {
        "url": url,
        "meta": {
            "provider": "nano_banana",
            "tool": "generate",
            **meta
        },
        "debug": {
            "prompt": final_prompt
        }
    }
