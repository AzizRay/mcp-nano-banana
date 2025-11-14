# server.py — FastAPI HTTP bridge for Nano Banana (Gemini 2.5)
import os, base64, requests, json, hashlib, time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from dotenv import load_dotenv
import google.generativeai as genai
from prompt_builder import compose_prompt

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CLOUDINARY_URL = os.getenv("CLOUDINARY_URL", "")  # cloudinary://<key>:<secret>@<cloud>

if not GEMINI_API_KEY: raise RuntimeError("GEMINI_API_KEY missing")
if not CLOUDINARY_URL: raise RuntimeError("CLOUDINARY_URL missing")

genai.configure(api_key=GEMINI_API_KEY)
# Use the image generation model
MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image-preview")

app = FastAPI()

# Load Prompt Builder spec once
try:
    with open("promptBuilderSpec.json", "r") as f:
        PROMPT_SPEC = json.load(f)
except FileNotFoundError:
    PROMPT_SPEC = None

class GenerateReq(BaseModel):
    prompt: Optional[str] = None
    builder: Optional[str] = None
    params: Dict[str, Any] = {}

def upload_cloudinary_b64(b64_png: str, folder="ayoon/imageops"):
    """
    Upload base64 image to Cloudinary using signed upload.
    CLOUDINARY_URL format: cloudinary://api_key:api_secret@cloud_name
    """
    try:
        # Parse Cloudinary URL: cloudinary://key:secret@cloud
        parts = CLOUDINARY_URL.replace("cloudinary://", "").split("@")
        if len(parts) != 2:
            raise ValueError("Invalid CLOUDINARY_URL format")
        
        credentials = parts[0].split(":")
        if len(credentials) != 2:
            raise ValueError("Invalid CLOUDINARY_URL credentials format")
        
        api_key = credentials[0]
        api_secret = credentials[1]
        cloud_name = parts[1]
        
        # Prepare parameters for signed upload
        timestamp = int(time.time())
        params = {
            "timestamp": timestamp,
            "folder": folder,
        }
        
        # Create signature for signed upload
        # Signature is SHA1 of: folder={folder}&timestamp={timestamp}{api_secret}
        param_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        signature_string = param_string + api_secret
        signature = hashlib.sha1(signature_string.encode()).hexdigest()
        
        # Cloudinary signed upload
        r = requests.post(
            f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload",
            data={
                "file": f"data:image/png;base64,{b64_png}",
                "api_key": api_key,
                "timestamp": timestamp,
                "signature": signature,
                "folder": folder
            },
            timeout=60
        )
        
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"cloudinary_upload_failed: {r.text}")
        
        result = r.json()
        if "secure_url" not in result:
            raise HTTPException(status_code=502, detail=f"cloudinary_upload_failed: Missing secure_url in response: {result}")
        
        return result["secure_url"]
    except ValueError as e:
        raise HTTPException(status_code=500, detail=f"cloudinary_config_error: {str(e)}")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"cloudinary_upload_failed: {str(e)}")

@app.get("/health")
def health():
    return {"ok": True, "service": "nano-banana-bridge", "builder": bool(PROMPT_SPEC)}

@app.post("/generate")
def generate_image(req: GenerateReq):
    """
    Accepts EITHER:
      { "prompt": "freeform…" }
    OR:
      { "builder": "product_angle_synthesis", "params": { ... } }
    """
    start_time = time.time() * 1000  # Start time in milliseconds
    status = "error"
    debug_info = {}
    
    try:
        # 1) Resolve prompt
        if req.prompt:
            final_prompt = req.prompt
            builder_meta = {}
        elif req.builder == "product_angle_synthesis":
            if not PROMPT_SPEC:
                raise HTTPException(status_code=500, detail="prompt_builder_spec_not_found")
            built = compose_prompt(PROMPT_SPEC, req.params or {})
            final_prompt = built["prompt"]
            builder_meta = built["meta"]
        else:
            raise HTTPException(
                status_code=400, 
                detail="invalid_input: Provide either 'prompt' or 'builder'='product_angle_synthesis'"
            )
        
        # Store the actual prompt used for generation
        actual_prompt = f"Generate a high-quality, detailed image of: {final_prompt}"
        
        # 2) Generate image with Gemini
        model = genai.GenerativeModel(MODEL)
        # Text → Image
        response = model.generate_content([actual_prompt])
        
        if not response:
            raise HTTPException(status_code=502, detail="gemini_no_image_returned")
        
        response_dict = response.to_dict()
        
        # Validate response structure
        if "candidates" not in response_dict or not response_dict["candidates"]:
            raise HTTPException(
                status_code=502, 
                detail=f"gemini_no_image_returned: No candidates in response. Response keys: {list(response_dict.keys())}"
            )
        
        candidate = response_dict["candidates"][0]
        if "content" not in candidate:
            raise HTTPException(
                status_code=502, 
                detail=f"gemini_invalid_response_structure: No content in candidate. Candidate keys: {list(candidate.keys())}"
            )
        
        if "parts" not in candidate["content"]:
            raise HTTPException(
                status_code=502, 
                detail=f"gemini_invalid_response_structure: No parts in content. Content keys: {list(candidate['content'].keys())}"
            )
        
        parts = candidate["content"]["parts"]
        if not parts:
            raise HTTPException(status_code=502, detail="gemini_no_image_returned: Empty parts array")
        
        # Find the part with image data (it might not be the last one)
        image_part = None
        for part in parts:
            if "inline_data" in part and "data" in part.get("inline_data", {}):
                image_part = part
                break
        
        if not image_part:
            # Log what we actually got for debugging
            part_types = [list(p.keys()) for p in parts]
            raise HTTPException(
                status_code=502, 
                detail=f"gemini_no_image_returned: No image data found in parts. Part keys: {part_types}"
            )
        
        last_part = image_part
        
        b64 = last_part["inline_data"]["data"]
        
        # 3) Upload to Cloudinary
        url = upload_cloudinary_b64(b64)
        
        # Calculate duration
        end_time = time.time() * 1000  # End time in milliseconds
        duration_ms = int(end_time - start_time)
        
        # Build standardized metadata
        meta = {
            "tool": "synthesize_angle",
            "provider": "gemini_nano_banana",
            "duration_ms": duration_ms,
            "status": "success",
            "prompt": actual_prompt
        }
        
        # Keep builder meta only in debug (not merged into main meta)
        if builder_meta:
            debug_info["builder_meta"] = builder_meta
        
        status = "success"
        
        return {
            "url": url,
            "meta": meta,
            "debug": debug_info if debug_info else None
        }
    except HTTPException as e:
        # Calculate duration even on error
        end_time = time.time() * 1000
        duration_ms = int(end_time - start_time)
        
        # Build error metadata
        meta = {
            "tool": "synthesize_angle",
            "provider": "gemini_nano_banana",
            "duration_ms": duration_ms,
            "status": "error",
            "prompt": actual_prompt if 'actual_prompt' in locals() else None
        }
        
        debug_info["error"] = str(e.detail) if hasattr(e, 'detail') else str(e)
        
        # Return error response with metadata (always 200 for API consistency)
        return {
            "url": None,
            "meta": meta,
            "debug": debug_info
        }
    except Exception as e:
        # Calculate duration even on error
        end_time = time.time() * 1000
        duration_ms = int(end_time - start_time)
        
        # Build error metadata
        meta = {
            "tool": "synthesize_angle",
            "provider": "gemini_nano_banana",
            "duration_ms": duration_ms,
            "status": "error",
            "prompt": actual_prompt if 'actual_prompt' in locals() else None
        }
        
        debug_info["error"] = str(e)
        
        # Return error response with metadata
        return {
            "url": None,
            "meta": meta,
            "debug": debug_info
        }
