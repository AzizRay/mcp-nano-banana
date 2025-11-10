# server.py — FastAPI HTTP bridge for Nano Banana (Gemini 2.5)
import os, base64, requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import google.generativeai as genai

# Load environment variables from .env file (for local dev)
load_dotenv()

# Get environment variables (will be empty if not set)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CLOUDINARY_URL = os.getenv("CLOUDINARY_URL", "")  # cloudinary://<key>:<secret>@<cloud>
MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.0-flash-exp")

# Configure Gemini only if API key is available
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

app = FastAPI()

# Add CORS middleware to allow requests from Vercel
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific Vercel domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    """Health check endpoint that shows configuration status"""
    has_gemini = bool(GEMINI_API_KEY)
    has_cloudinary = bool(CLOUDINARY_URL)
    return {
        "ok": has_gemini and has_cloudinary,
        "service": "nano-banana-bridge",
        "config": {
            "gemini_api_key": "configured" if has_gemini else "missing",
            "cloudinary_url": "configured" if has_cloudinary else "missing"
        }
    }

class GenerateReq(BaseModel):
    prompt: str
    
    model_config = {
        "extra": "ignore"  # Allow extra fields for flexibility
    }

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
    # Validate request
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(
            status_code=422,
            detail="Missing or empty 'prompt' field in request body. Expected: {'prompt': 'your text here'}"
        )
    
    # Validate environment variables
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY environment variable is not set. Please configure it in Railway dashboard."
        )
    if not CLOUDINARY_URL:
        raise HTTPException(
            status_code=500,
            detail="CLOUDINARY_URL environment variable is not set. Please configure it in Railway dashboard."
        )
    
    try:
        model = genai.GenerativeModel(MODEL)
        # Text → Image
        res = model.generate_images(req.prompt)
        if not res or not res.generated_images:
            raise HTTPException(status_code=502, detail="gemini_no_image_returned")
        b64 = res.generated_images[0].image_base64
        url = upload_cloudinary_b64(b64)
        return {"url": url, "provider": "nano_banana", "tool": "generate"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"gemini_error: {str(e)}")
