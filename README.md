# MCP Nano Banana

**PyPI Version**

This project is an MCP (Model Context Protocol) server that generates images using the Google Gemini API.

## Description

This server implements the Model Context Protocol to expose a single tool, `generate_image`, to a compatible AI model. The tool accepts a text prompt, uses the Google Gemini API to generate an image, saves the image to the `public/` directory for auditing, and returns the raw image data as a base64-encoded string.

### To use the server with Claude Desktop or other applications

You need a Google Gemini API key and ImgBB API key to use this server.

Access [https://api.imgbb.com/](https://api.imgbb.com/) to generate an IMGBB API Key. This is used to store and host the image online.

```json
{
  "mcpServers": {
    "mcp-nano-banana": {
      "command": "uvx",
      "args": ["mcp-nano-banana"],
      "env": {
        "GEMINI_API_KEY": "YOUR_API_KEY_HERE",
        "IMGBB_API_KEY": "YOUR_API_KEY_HERE"
      }
    }
  }
}
```

## Dev Setup

### 1. Dependencies

This project uses Python and its dependencies are defined in `pyproject.toml`. You can install them using pip:

```bash
pip install .
# Or
uv sync
```

This will install `mcp`, `google-generativeai`, and other required packages.

### 2. API Key

You need a Google Gemini API key and ImgBB API key to use this server.

Access [https://api.imgbb.com/](https://api.imgbb.com/) to generate an IMGBB API Key. This is used to store and host the image online.

Create a file named `.env` in the root of the project. Add your API key to the `.env` file in the following format:

```
GEMINI_API_KEY="YOUR_API_KEY_HERE"
IMGBB_API_KEY="YOUR_API_KEY_HERE"
```

## Running the Server

This server is designed to be run as a subprocess by an MCP client or using the mcp command-line tool. The server listens for requests on stdio.

```bash
uvx --from git+https://github.com/GuilhermeAumo/mcp-nano-banana mcp-nano-banana
```

## Publishing new PyPI version

To publish a new version of this package to PyPI:

**Update the version**
Edit the version field in `pyproject.toml` to the new version number.

**Build the package**

```bash
uv build
```

This will create `.tar.gz` and `.whl` files in the `dist/` directory.

**Upload to PyPI**

```bash
uv publish
```

**Tag the release** (optional but recommended)
Commit the changes to GitHub first, then:

```bash
git tag v<new-version>
git push --tags
```

**Note:**
You need a PyPI account and must be listed as a maintainer of the project.
For more details, see the Python Packaging User Guide.

---

## 🚧 Ayoon Fork — What’s added in this repo

This fork adds a small **HTTP bridge** so non-MCP clients (e.g., our Vercel façade) can call Nano-Banana over REST. It supports:

* `GET /health` — basic readiness probe
* `POST /generate` — accepts either a **raw prompt** or a **Prompt-Builder payload** and returns a **Cloudinary URL** (once keys are set)

This bridge is used by the **ImageOps façade** here:
🔗 [https://github.com/AzizRay/imageops-mcp](https://github.com/AzizRay/imageops-mcp)

High-level flow:

```
n8n → ImageOps (Vercel) → /api/tools/synthesize_angle
     ↳ Nano-Banana Bridge (this repo, FastAPI on Railway) → Gemini → Cloudinary → { url, meta }
```

### Prompt Builder (deterministic angles)

The repo includes:

* `promptBuilderSpec.json` — defines catalog-ready angle presets (front, ¾, side, top), lighting, bg, shadow, etc.
* `prompt_builder.py` — tiny renderer (no deps) supporting `{{var}}` and `{{#if ...}}` blocks.

Send either:

* `{ "prompt": "Studio product photo …" }`, **or**
* `{ "builder": "product_angle_synthesis", "params": { ... } }`

---

## Bridge API (FastAPI)

### Environment variables (bridge)

```
GEMINI_API_KEY=<gemini_key>                 # required
CLOUDINARY_URL=cloudinary://<key>:<secret>@<cloud_name>  # required for URL output
```

*Optional (future):* `STABILITY_API_KEY`, `REPLICATE_API_TOKEN`, etc.

### Run locally

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

### Endpoints

#### Health

```
GET /health
→ 200 { "ok": true, "service": "nano-banana-bridge", "builder": true }
```

#### Generate (two shapes)

**A) Structured (Prompt-Builder)**

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{
    "builder": "product_angle_synthesis",
    "params": {
      "productName": "leather bifold wallet",
      "angle": "front-45",
      "background": "neutral-plinth",
      "lighting": "soft-studio",
      "shadow": "proportional-soft",
      "focal": "70mm",
      "ar": "1:1"
    }
  }'
```

**B) Raw prompt**

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Studio product photo of a leather wallet, front 45°, soft studio light, neutral plinth, proportional soft shadow, 70mm, 1:1."}'
```

**Response (once keys wired):**

```json
{
  "url": "https://res.cloudinary.com/<cloud>/image/upload/...",
  "meta": { "provider": "nano_banana", "tool": "generate", "angle": "front-45" },
  "debug": { "prompt": "..." }
}
```

---

## Relationship to ImageOps (Vercel façade)

* **This repo** exposes `/generate` and returns a Cloudinary URL.
* **ImageOps** (Vercel) exposes `/api/tools/synthesize_angle` and **forwards the request body verbatim** to this bridge, then returns the bridge JSON to clients.
* Other image tools (bg-remove/enhance/upscale/smart-fit) are handled in ImageOps via Rembg/Stability/Cloudinary.

Façade repo: [https://github.com/AzizRay/imageops-mcp](https://github.com/AzizRay/imageops-mcp)

---

## Security

* Keep this repo **private**.
* Do **not** commit `.env` or secrets.
* Railway / server logs should **not** include API keys or full prompts with PII.

---

## Roadmap

* Add optional endpoints: `/inpaint`, `/outpaint`, `/object-remove`, `/relight`.
* Optional provider stacking (e.g., Stability edits as utilities here).
* Add rate-limit + minimal request metrics.
* Unit tests for Prompt-Builder and basic endpoint I/O.

---
