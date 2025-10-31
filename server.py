# server.py — FastAPI HTTP bridge for Nano Banana (Gemini 2.5)
import os, base64, requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import google.generativeai as genai

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CLOUDINARY_URL = os.getenv("CLOUDINARY_URL", "")  # cloudinary://<key>:<secret>@<cloud>

if not GEMINI_API_KEY: raise RuntimeError("GEMINI_API_KEY missing")
if not CLOUDINARY_URL: raise RuntimeError("CLOUDINARY_URL missing")

genai.configure(api_key=GEMINI_API_KEY)
# Fast, good default; change later if needed
MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.0-flash-exp")

app = FastAPI()

class GenerateReq(BaseModel):
    prompt: str

def upload_cloudinary_b64(b64_png: str, folder="ayoon/imageops"):
    cloud = CLOUDINARY_URL.split("@")[1]
    key   = CLOUDINARY_URL.split("//")[1].split(":")[0]
    secret= CLOUDINARY_URL.split(":")[2].split("@")[0]
    r = requests.post(
        f"https://api.cloudinary.com/v1_1/{cloud}/image/upload",
        auth=(key, secret),
        data={"file": "data:image/png;base64," + b64_png, "folder": folder}
    )
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="cloudinary_upload_failed: "+r.text)
    return r.json()["secure_url"]

@app.post("/generate")
def generate_image(req: GenerateReq):
    try:
        model = genai.GenerativeModel(MODEL)
        # Text → Image
        res = model.generate_images(req.prompt)
        if not res or not res.generated_images:
            raise HTTPException(502, "gemini_no_image_returned")
        b64 = res.generated_images[0].image_base64
        url = upload_cloudinary_b64(b64)
        return {"url": url, "provider": "nano_banana", "tool": "generate"}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"gemini_error: {e}")
