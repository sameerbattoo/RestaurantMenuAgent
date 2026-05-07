# 🍽️ Restaurant Menu Assistant

An AI-powered restaurant menu processing system built on **AWS Bedrock AgentCore Runtime** using the **Strands Agents SDK**. Upload restaurant menu documents (PDFs, images), extract structured data, edit items, merge multi-page menus, and regenerate styled HTML versions — all through a conversational web interface.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              USER (Browser)                                  │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │    CloudFront CDN   │──── Serves React UI + Generated HTMLs
                    │  (S3 Origin + OAC)  │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼────────-┐      │      ┌─────────▼───────-──┐
    │   S3 Bucket       │      │      │   Cognito          │
    │ ├─ /              │      │      │   User Pool        │
    │ │  (React UI)     │      │      │   (OAuth 2.0)      │
    │ ├─ menu-uploads/  │      │      │                    │
    │ │  originals/     │      │      │  ┌──────────────┐  │
    │ │  generated/     │      │      │  │ JWT Validate │  │
    │ └─────────────────│      │      │  └──────┬───────┘  │
    └───────────────────┘      │      └─────────┼──────────┘
                               │                │
                    ┌──────────▼────────────────▼──────────┐
                    │                                      │
                    │   AWS Bedrock AgentCore Runtime      │
                    │   ┌───────────────────────────────┐  │
                    │   │  Restaurant Menu Agent        │  │
                    │   │  (Strands SDK / Python)       │  │
                    │   │                               │  │
                    │   │  Tools:                       │  │
                    │   │  • process_document           │  │
                    │   │  • add/remove/update_item     │  │
                    │   │  • merge_menu                 │  │
                    │   │  • regenerate_menu_html       │  │
                    │   │  • analyze_menu_style         │  │
                    │   │  • list/get/export menus      │  │
                    │   └───────────┬───────────────────┘  │
                    │               │                      │
                    └───────────────┼──────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
    ┌─────────▼──────-───┐ ┌────────▼────────┐ ┌─────────▼─────────┐
    │  Amazon Bedrock    │ │   DynamoDB      │ │  AgentCore Memory │
    │                    │ │                 │ │                   │
    │  Sonnet 4.6        │ │  restaurant-    │ │  Conversation     │
    │  (Vision + Orch)   │ │  menus table    │ │  persistence +    │
    │                    │ │                 │ │  user preferences │
    │  Haiku 4.5         │ │  PK: file_name  │ │                   │
    │  (Text→JSON)       │ │  Menu data +    │ │                   │
    │                    │ │  style + S3 ref │ │                   │
    └────────────────────┘ └─────────────────┘ └───────────────────┘
```

### Data Flow

1. **User authenticates** via Cognito Hosted UI → receives OAuth access token
2. **Browser sends requests** to AgentCore Runtime with Bearer token + Session ID header
3. **AgentCore validates JWT** against Cognito OIDC discovery URL
4. **Agent processes request** — streams SSE responses back to browser
5. **For file uploads**: saved to S3 → extracted with Bedrock → structured JSON saved to DynamoDB
6. **For menu regeneration**: style analyzed from S3 original → HTML generated via template → uploaded to S3 → CloudFront URL returned

---

## Features

### Menu Processing
- **Upload & Extract** — Upload PDFs or images (JPG, PNG, HEIC, TIFF, WEBP). The agent extracts all dishes, prices, descriptions, and dietary info into structured data.
- **Parallel Processing** — Multiple files processed simultaneously using parallel tool calls.
- **Conflict Detection** — Automatically detects if a restaurant already exists in the database and recommends merge or overwrite based on item overlap analysis.

### Menu Management
- **Add/Remove/Update Items** — Edit individual menu items with instant DynamoDB persistence.
- **Rename Restaurant/Category** — Lightweight rename operations without re-processing.
- **Merge Menus** — Combine multiple files from the same restaurant (e.g., different pages) into a single entry. Tracks source files in metadata.
- **List & Export** — View all stored menus, export full structured data.

### Menu Regeneration
- **Style Analysis** — Uses Bedrock vision to analyze the original menu's visual design (colors, fonts, layout, borders, dividers).
- **HTML Generation** — Generates a styled, responsive HTML menu using a Python template engine (instant, no LLM needed). Supports single/two/three-column layouts, page breaks for print, and respects the original style.
- **CloudFront Download** — Generated HTML uploaded to S3 with unique timestamped URLs served via CloudFront.

### Conversation & Memory
- **Streaming Responses** — Real-time SSE streaming with tool progress indicators.
- **Session Persistence** — In-container agent caching + AgentCore Memory for cross-session context.
- **Personalization** — Remembers user preferences and past interactions via long-term memory strategies.

---

## Project Structure

```
restaurant-menu-assistant/
├── agent/                          # Deployed agent (AgentCore Runtime)
│   ├── main.py                     # Entrypoint — async streaming, session management
│   ├── document_processor.py       # Menu extraction (Bedrock vision + Haiku structuring)
│   ├── menu_tools.py               # CRUD tools (DynamoDB-backed)
│   ├── menu_generator.py           # Style analysis + HTML template generation
│   ├── memory_hook.py              # AgentCore Memory integration
│   ├── file_handler.py             # Upload handling (temp storage + S3)
│   ├── s3_storage.py               # S3 operations (upload, download, CloudFront URLs)
│   ├── metrics.py                  # Token usage accumulator
│   ├── utils.py                    # Shared utilities (timed_tool decorator)
│   └── requirements.txt            # Agent runtime dependencies
│
├── webui/                          # React frontend (Vite + Tailwind)
│   ├── src/
│   │   ├── components/             # ChatView, LoginPage, MarkdownMessage, etc.
│   │   ├── services/               # agent.js (SSE client), auth.js (Cognito OAuth)
│   │   └── hooks/                  # useTheme (dark/light mode)
│   └── .env.example                # Configuration template
│
├── batch_processing/               # Offline batch processing & model comparison
│   ├── batch_process_menus.py      # Process all sample menus (parallel, direct calls)
│   ├── document_processor.py       # Standalone extractor (no DynamoDB)
│   ├── compare_runs.py             # Generate markdown comparison reports
│   └── batch_runs/                 # Output folders per run + comparison-report.md
│
├── infra/                          # Deployment scripts (numbered, sequential)
│   ├── 01-setup-cognito.sh         # Cognito user pool configuration
│   ├── 02-configure-agentcore.sh   # AgentCore one-time setup
│   ├── 03-setup-cloudfront.sh      # S3 bucket + CloudFront distribution
│   ├── 04-setup-dynamodb.sh        # DynamoDB table + IAM permissions
│   ├── 05-setup-memory.sh          # AgentCore Memory + IAM permissions
│   ├── 06-deploy-webui.sh          # Build & deploy React app
│   └── 07-launch-agentcore.sh      # Deploy agent container with env vars
│
├── sample_menus/                   # Test menu files (PDFs, HEIC, JPG, PNG)
└── requirements.txt                # Local development dependencies
```

---

## Deployment

### Prerequisites
- AWS account with Bedrock model access (Claude Sonnet 4, Haiku 4.5)
- AWS CLI configured
- Node.js 18+ (for web UI)
- Python 3.11+ (for agent)
- `agentcore` CLI installed (`pip install bedrock-agentcore-starter-toolkit`)

### Step-by-Step

```bash
cd infra/

# 1. Configure Cognito (discovers existing user pool)
bash 01-setup-cognito.sh

# 2. Configure AgentCore (one-time — sets up execution role, ECR, etc.)
bash 02-configure-agentcore.sh

# 3. Create CloudFront distribution + S3 bucket
bash 03-setup-cloudfront.sh

# 4. Create DynamoDB table + grant permissions (S3, Bedrock, DynamoDB)
bash 04-setup-dynamodb.sh

# 5. Create AgentCore Memory for conversation persistence
bash 05-setup-memory.sh

# 6. Build and deploy the React web UI
bash 06-deploy-webui.sh

# 7. Deploy the agent container (with all env vars)
bash 07-launch-agentcore.sh
```

All scripts are idempotent — safe to re-run. Configuration is saved to `infra/.cognito-config` and shared between scripts.

### Environment Variables (Agent)

| Variable | Description |
|----------|-------------|
| `AWS_REGION` | AWS region (default: us-west-2) |
| `DYNAMODB_TABLE` | DynamoDB table name (default: restaurant-menus) |
| `AGENTCORE_MEMORY_ID` | AgentCore Memory ID for conversation persistence |
| `MENU_S3_BUCKET` | S3 bucket for uploads and generated HTML |
| `CLOUDFRONT_DOMAIN` | CloudFront domain for download URLs |
| `BEDROCK_MODEL_ID` | Vision model (default: us.anthropic.claude-sonnet-4-6) |

---

## Model Selection & Optimization

### Processing Pipeline

```
PDF Upload                          Image Upload
    │                                   │
    ▼                                   ├── HEIC/HEIF? ──► Convert to JPEG
PyPDF Text Extraction (instant)         │                       │
    │                                   ├── > 2048px? ──► Resize (Lanczos)
    ├── Text found?                     │                       │
    │   YES ──► Haiku 4.5 (Text→JSON)  ▼                       ▼
    │           (fast: ~$0.01/file)     Sonnet 4.6 — Vision → JSON
    │                                  (accurate: ~$0.03-0.05/file)
    │   NO (scanned PDF) ─────────────────────┘
    │                                          │
    └──────────────────┬───────────────────────┘
                       ▼
               Price Validation
               (reject if >50% items lack price,
                filter individual no-price items)
                       │
                       ▼
               Conflict Detection
               (fuzzy match restaurant name in DB)
                       │
                       ▼
               Save to DynamoDB + Upload to S3
               Return summary
```

### Why This Architecture

1. **PDFs with extractable text** → PyPDF (free, instant) + Haiku for structuring (fast, $0.80/1M input). No vision needed.
2. **Scanned PDFs / Images** → Bedrock Converse API with Sonnet vision (best accuracy for complex menus).
3. **HTML Regeneration** → Python template (instant, zero tokens). Only style analysis uses an LLM call (once, cached).

### Agent Model: Sonnet 4.6 (Converse API)

The agent uses **Claude Sonnet 4.6** for vision-based menu extraction. Because all Bedrock calls use the **Converse API** (model-agnostic), the model can be switched to any Bedrock-supported model (Opus, Nova Pro, Haiku, etc.) by changing the `BEDROCK_MODEL_ID` environment variable — no code changes required.

> **Note on Textract:** If you want to use the Textract TABLES pipeline (highest item coverage, best dietary detection) in the agent, the pipeline built in `batch_processing/document_processor.py` needs to be integrated into `agent/document_processor.py`. This is a future enhancement — the batch processing folder contains the complete, tested implementation ready for integration.

### Model Comparison Results

From batch processing 12 restaurant menus (PDFs + images) with all 4 extraction methods:

| Metric | Sonnet 4.6 | Opus 4.7 | Nova Pro | Textract + Haiku |
|--------|-----------|----------|----------|-----------------|
| Success Rate | **12/12** | **12/12** | **12/12** | **12/12** |
| Duration | 82s | 84s | **69s** | 88s |
| Items Extracted | 599 | **634** | 596 | 610 |
| Items with Price | 93% | **95%** | 92% | 91% |
| Dietary Info | 59% | 54% | 29% | **66%** |
| Descriptions | 65% | **70%** | 65% | 57% |
| **Total Cost** | $0.90 | $3.62 | **$0.32** | $0.63 |
| Cost per Item | $0.0015 | $0.0057 | **$0.0005** | $0.0010 |

**Recommendation:** Sonnet 4.6 for the agent (production) — best balance of accuracy, reliability, and cost. Textract pipeline is the best choice for bulk/batch processing where maximum item coverage matters.

### Key Optimizations

- **Dynamic max_tokens** — Scales output limit based on model (Nova: 10K, Haiku: 8K, Anthropic: 16K) to avoid truncation.
- **Converse API** — Model-agnostic API that works with all Bedrock models without format branching.
- **JSON repair** — Handles common LLM malformations (missing quotes, markdown fences, truncated output).
- **Extraction cache** — Second calls (overwrite/merge confirmation) skip re-processing using in-memory cache.
- **Adaptive retries** — boto3 adaptive retry mode with exponential backoff on all Bedrock calls.
- **Parallel tool execution** — Multiple files processed simultaneously via Strands parallel tool calls.
- **Price validation** — Rejects files where >50% of items lack pricing; filters individual no-price items before saving.

---

## Batch Processing

The `batch_processing/` folder is a standalone test harness for comparing extraction methods without the agent. It calls document processing functions directly for maximum speed and supports 4 extraction methods.

### Supported Models

| Model | Method | Best For |
|-------|--------|----------|
| `sonnet` | LLM Vision (Converse API) | Production accuracy |
| `opus` | LLM Vision (Converse API) | Maximum item extraction |
| `nova-pro` | LLM Vision (Converse API) | Lowest cost, fastest |
| `textract` | Textract TABLES → Haiku structuring | Best item coverage + dietary detection |

### Textract Pipeline

The Textract pipeline uses a two-stage approach:

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────┐     ┌──────────┐
│  Menu File  │ ──► │  AWS Textract        │ ──► │  Table Parser   │ ──► │  Haiku   │ ──► JSON
│ (image/PDF) │     │  (TABLES feature)    │     │  (Python logic) │     │  (LLM)   │
└─────────────┘     └──────────────────────┘     └─────────────────┘     └──────────┘
```

1. **Textract TABLES** — Detects table structures in the menu (item names aligned with prices). Works even without grid borders because menus are visually tabular.
2. **Table Parser** — Extracts row/column cells, resolves WORD children, separates non-table lines (headers, restaurant name).
3. **Formatted Output** — Produces `Item Name | Price` lines that give Haiku unambiguous pairing.
4. **Haiku Structuring** — Converts the formatted text into the standard JSON schema.

**Why TABLES?** Benchmarked all Textract FeatureTypes on 12 menus: TABLES extracted 607 items (matching Sonnet's 608), while LAYOUT only got 320 (prices disconnected from items).

**Routing:** Single-page files use the sync API (send bytes directly). Multi-page PDFs use the async API (upload to S3 → poll → paginate → cleanup).

### Usage

```bash
cd batch_processing/

# Run all 4 models and generate comparison report (one-click)
bash run_all_models.sh

# Or run individual models
python batch_process_menus.py --model sonnet
python batch_process_menus.py --model textract
python batch_process_menus.py --model nova-pro
python batch_process_menus.py --model opus

# Custom options
python batch_process_menus.py --model sonnet --workers 3 --dir /path/to/menus

# Generate comparison report (last N runs)
python compare_runs.py --last 4 -o batch_runs/comparison-report.md
```

---

## Web UI

The React frontend provides:
- **Cognito OAuth 2.0** login flow
- **Dark/Light mode** with system preference detection
- **File upload** with drag & drop (multi-file support)
- **Streaming responses** with real-time tool progress indicators
- **Markdown rendering** with tables, code blocks, and styled content
- **Token metrics** displayed per response (input/output tokens, cost, latency, model)
- **Cooking animation** while processing menu files
- **New Chat** button for fresh sessions

---

## Security

- **Cognito JWT authentication** — All agent requests require a valid access token
- **S3 path sanitization** — File names sanitized with `os.path.basename()` to prevent traversal
- **OAC (Origin Access Control)** — S3 bucket only accessible via CloudFront
- **IAM least privilege** — Separate policies for DynamoDB, S3, Bedrock, and Memory access
- **No secrets in code** — All configuration via environment variables or `.cognito-config`

---

## License

Internal project — not for public distribution.
