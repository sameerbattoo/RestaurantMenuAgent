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
PDF Upload                          Image Upload (JPG/HEIC/PNG)
    │                                       │
    ▼                                       ▼
PyPDF Text Extraction (instant)     Resize if > 2048px
    │                                       │
    ▼                                       ▼
Haiku 4.5 — Text → JSON            Sonnet 4.6 — Vision → JSON
(fast, cheap: ~$0.01/file)          (accurate: ~$0.03-0.05/file)
    │                                       │
    └───────────────┬───────────────────────┘
                    ▼
            Save to DynamoDB
            Upload to S3
            Return summary
```

### Why This Architecture

1. **PDFs with extractable text** → PyPDF (free, instant) + Haiku for structuring (fast, $0.80/1M input). No vision needed.
2. **Scanned PDFs / Images** → Bedrock Converse API with Sonnet vision (best accuracy for complex menus).
3. **HTML Regeneration** → Python template (instant, zero tokens). Only style analysis uses an LLM call (once, cached).

### Model Comparison Results

From batch processing 12 restaurant menus (PDFs + images):

| Metric | Sonnet 4.6 | Opus 4.7 | Nova Pro |
|--------|-----------|----------|----------|
| Success Rate | **12/12** | **12/12** | 11/12 |
| Duration | **81s** | 81s | 82s |
| Items Extracted | **608** | 593 | 666* |
| Cost | **$0.92** | $3.63 | $0.34 |
| Dietary Info | **57%** | 56% | 27% |
| Descriptions | 64% | **69%** | 67% |

*Nova Pro extracts more items but misses dietary info and occasionally produces malformed JSON.

**Recommendation:** Sonnet 4.6 for production — best reliability, quality-to-cost ratio, and consistent JSON output.

### Key Optimizations

- **Dynamic max_tokens** — Scales output limit based on model (Nova: 10K, Anthropic: 16K) to avoid truncation.
- **Converse API** — Model-agnostic API that works with all Bedrock models without format branching.
- **JSON repair** — Handles common LLM malformations (missing quotes, markdown fences, truncated output).
- **Extraction cache** — Second calls (overwrite/merge confirmation) skip re-processing using in-memory cache.
- **Adaptive retries** — boto3 adaptive retry mode with exponential backoff on all Bedrock calls.
- **Parallel tool execution** — Multiple files processed simultaneously via Strands parallel tool calls.

---

## Batch Processing

Run model comparisons locally without the agent:

```bash
cd batch_processing/

# Process all sample menus with Sonnet (vision)
python batch_process_menus.py --model sonnet

# Try with Nova Pro (cheaper but less reliable)
python batch_process_menus.py --model nova-pro

# Try with Opus (most expensive)
python batch_process_menus.py --model opus

# Generate comparison report
python compare_runs.py -o batch_runs/comparison-report.md

# Compare only last 2 runs
python compare_runs.py --last 2
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
