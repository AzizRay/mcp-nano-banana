# server.py — FastAPI HTTP bridge for Nano Banana (Gemini 2.5)
import os, base64, requests, json, hashlib, time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from dotenv import load_dotenv
import google.generativeai as genai
from prompt_builder import compose_prompt
from io import BytesIO
from PIL import Image
from urllib.parse import urlparse

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

class EditImageReq(BaseModel):
    image_url: str
    prompt: str

def resize_image_b64(b64_image: str, target_width: int = 720, target_height: int = 1280) -> str:
    """
    Resize base64 image to exactly target dimensions (720x1280).
    Uses resize and crop to fill the entire canvas.
    Returns base64 string of resized image.
    """
    try:
        # Decode base64 to image
        image_data = base64.b64decode(b64_image)
        image = Image.open(BytesIO(image_data))
        
        # Convert to RGB if necessary
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Calculate scaling to fill the target dimensions (cover, not fit)
        # We want to resize so the image covers the entire 720x1280 area
        width_ratio = target_width / image.width
        height_ratio = target_height / image.height
        
        # Use the larger ratio to ensure the image covers the entire area
        scale_ratio = max(width_ratio, height_ratio)
        
        # Resize the image
        new_width = int(image.width * scale_ratio)
        new_height = int(image.height * scale_ratio)
        resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Crop to exact target dimensions (center crop)
        left = (new_width - target_width) // 2
        top = (new_height - target_height) // 2
        right = left + target_width
        bottom = top + target_height
        
        cropped_image = resized_image.crop((left, top, right, bottom))
        
        # Convert back to base64
        buffer = BytesIO()
        cropped_image.save(buffer, format='PNG')
        resized_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        return resized_b64
    except Exception as e:
        raise ValueError(f"Image resize error: {str(e)}")

def upload_cloudinary_b64(b64_png: str, folder="ayoon/imageops"):
    """
    Upload base64 image to Cloudinary using signed upload.
    Images are resized to 720x1280 before upload.
    CLOUDINARY_URL format: cloudinary://api_key:api_secret@cloud_name
    """
    try:
        # Resize image to 720x1280 before uploading
        b64_png = resize_image_b64(b64_png, target_width=720, target_height=1280)
        
        # Parse Cloudinary URL: cloudinary://key:secret@cloud
        # Strip whitespace to handle trailing newlines
        url = CLOUDINARY_URL.strip()
        parts = url.replace("cloudinary://", "").split("@")
        if len(parts) != 2:
            raise ValueError("Invalid CLOUDINARY_URL format")
        
        credentials = parts[0].split(":")
        if len(credentials) != 2:
            raise ValueError("Invalid CLOUDINARY_URL credentials format")
        
        api_key = credentials[0].strip()
        api_secret = credentials[1].strip()
        cloud_name = parts[1].strip()
        
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

@app.post("/edit-image")
def edit_image(req: EditImageReq):
    """
    Takes an image URL and text prompt, generates a new image using Gemini API.
    Accepts:
      { "image_url": "https://...", "prompt": "edit instruction..." }
    """
    start_time = time.time() * 1000  # Start time in milliseconds
    debug_info = {}
    
    try:
        # Validate image URL
        parsed_url = urlparse(req.image_url)
        if not parsed_url.scheme or not parsed_url.netloc:
            raise HTTPException(status_code=400, detail="invalid_image_url: Invalid URL format")
        
        if parsed_url.scheme not in ['http', 'https']:
            raise HTTPException(status_code=400, detail="invalid_image_url: URL must use HTTP or HTTPS")
        
        # 1) Download image from URL
        try:
            img_response = requests.get(req.image_url, timeout=30)
            img_response.raise_for_status()
            
            # Check content type
            content_type = img_response.headers.get('content-type', '').lower()
            if not any(img_type in content_type for img_type in ['image/', 'application/octet-stream']):
                raise HTTPException(status_code=400, detail=f"invalid_image_url: URL does not point to an image. Content-Type: {content_type}")
            
            # Check file size (10MB limit)
            if len(img_response.content) > 10 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="invalid_image_url: Image file too large (max 10MB)")
            
            image_data = img_response.content
            
            # Validate and process image
            try:
                image = Image.open(BytesIO(image_data))
                
                # Validate image format
                if image.format not in ['JPEG', 'PNG', 'WEBP', 'BMP', 'GIF']:
                    raise HTTPException(status_code=400, detail=f"invalid_image_format: Unsupported format: {image.format}")
                
                # Check image dimensions
                width, height = image.size
                if width > 4096 or height > 4096:
                    raise HTTPException(status_code=400, detail=f"invalid_image_size: Image too large: {width}x{height} (max 4096x4096)")
                
                if width < 1 or height < 1:
                    raise HTTPException(status_code=400, detail="invalid_image_size: Invalid image dimensions")
                
                # Convert to RGB if necessary
                if image.mode not in ['RGB', 'RGBA']:
                    image = image.convert('RGB')
                
            except Exception as e:
                if "cannot identify image file" in str(e).lower():
                    raise HTTPException(status_code=400, detail="invalid_image_format: Invalid or corrupted image file")
                else:
                    raise HTTPException(status_code=400, detail=f"image_processing_error: {str(e)}")
            
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=400, detail=f"image_download_error: {str(e)}")
        
        # 2) Generate new image with Gemini
        model = genai.GenerativeModel(MODEL)
        
        # Prepare the prompt and image for Gemini
        response = model.generate_content([req.prompt, image])
        
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
        
        # Find the part with image data
        image_part = None
        for part in parts:
            if "inline_data" in part and "data" in part.get("inline_data", {}):
                image_part = part
                break
        
        if not image_part:
            part_types = [list(p.keys()) for p in parts]
            raise HTTPException(
                status_code=502, 
                detail=f"gemini_no_image_returned: No image data found in parts. Part keys: {part_types}"
            )
        
        b64 = image_part["inline_data"]["data"]
        
        # 3) Upload to Cloudinary
        url = upload_cloudinary_b64(b64)
        
        # Calculate duration
        end_time = time.time() * 1000  # End time in milliseconds
        duration_ms = int(end_time - start_time)
        
        # Build standardized metadata
        meta = {
            "tool": "edit_image",
            "provider": "gemini_nano_banana",
            "duration_ms": duration_ms,
            "status": "success",
            "prompt": req.prompt
        }
        
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
            "tool": "edit_image",
            "provider": "gemini_nano_banana",
            "duration_ms": duration_ms,
            "status": "error",
            "prompt": req.prompt if 'req' in locals() else None
        }
        
        debug_info["error"] = str(e.detail) if hasattr(e, 'detail') else str(e)
        
        # Return error response with metadata
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
            "tool": "edit_image",
            "provider": "gemini_nano_banana",
            "duration_ms": duration_ms,
            "status": "error",
            "prompt": req.prompt if 'req' in locals() else None
        }
        
        debug_info["error"] = str(e)
        
        # Return error response with metadata
        return {
            "url": None,
            "meta": meta,
            "debug": debug_info
        }
