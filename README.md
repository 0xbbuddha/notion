# notion-c2

A [Mythic](https://github.com/its-a-feature/Mythic) C2 profile that uses **Notion** as a covert communication channel.

Agents communicate by reading/writing pages in a shared Notion database, making C2 traffic indistinguishable from normal SaaS usage — a _Living off Trusted Sites_ (LoTS) technique.

> **For authorized security testing and research only.**

---

## How it works

```
Agent                      Notion Database            C2 Profile Container
  │                              │                            │
  ├─── create page (dir=in) ────►│                            │
  │    base64(encrypted_data)    │◄── query unprocessed ──────┤
  │                              │                            ├──► Mythic Server
  │                              │◄── create page (dir=out) ──┤
  │◄── query page (dir=out) ─────┤    base64(response)        │
```

Each message is a Notion database page. The payload is stored in code blocks inside the page body, which avoids Notion's 2000-character property limit and supports large task outputs.

---

## Notion setup

### 1. Create an integration

Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) and create a new internal integration.
Copy the **Integration Token** (`ntn_...`).

### 2. Create the database

Create a new Notion database (full-page) with the following properties:

| Property name | Type     | Notes                                  |
|---------------|----------|----------------------------------------|
| `uuid`        | Title    | Auto-populated by the server           |
| `direction`   | Select   | Options: `in`, `out`                   |
| `agent_id`    | Text     | UUID of the agent                      |
| `processed`   | Checkbox | Checked once the message is consumed   |
| `size`        | Number   | Decoded payload size in bytes          |

> The `created_time` property is added automatically by Notion.

### 3. Share the database with your integration

Open the database → **Share** → invite your integration.

### 4. Get the database ID

The database ID is the 32-character string in the database URL:
```
https://notion.so/your-workspace/<DATABASE_ID>?v=...
```

---

## Server configuration

The `config.json` file is **written automatically by Mythic** at container startup using the parameters you set in the Mythic UI (C2 Profiles → notion → parameters). You do not need to create it manually.

A `config.json.example` is provided as a reference template.

## Installation in Mythic

```bash
cd /path/to/Mythic
./mythic-cli install github https://github.com/0xbbuddha/notion
```

Or manually:

```bash
./mythic-cli install folder /path/to/notion
```

---

## Configuration parameters

| Parameter           | Description                                         | Default |
|---------------------|-----------------------------------------------------|---------|
| `integration_token` | Notion integration token (`ntn_...`)             | —       |
| `database_id`       | ID of the shared Notion database                    | —       |
| `callback_interval` | Agent polling interval in seconds                   | `10`    |
| `callback_jitter`   | Jitter % applied to the polling interval (0–50)     | `10`    |

---

## Agent-side implementation

Your agent needs to implement two operations against the Notion API:

**Send data to server** (`POST /v1/pages`):
- Create a page in the database with `direction=in`, `agent_id=<uuid>`
- Store `base64(encrypted_data)` in code blocks in the page body

**Poll for responses** (`POST /v1/databases/{id}/query`):
- Filter: `direction=out`, `agent_id=<uuid>`, `processed=false`
- Read the code blocks from matching pages
- Decode and pass the data to Mythic's crypto layer
- Mark the page as processed (`PATCH /v1/pages/{id}`)

See `C2_Profiles/notion/c2_code/notion_client.py` for a reference implementation.

---

## Limitations

- Notion API rate limit: ~3 req/s — keep `callback_interval` ≥ 5s
- Large payloads are chunked into 1800-char blocks automatically
- Processed pages are archived automatically — inbound pages are archived immediately after processing, outbound pages are archived once the agent marks them as consumed

---

## Project structure

```
notion-c2/
├── config.json                              # mythic-cli install config
├── documentation-c2/
│   └── notion.md
├── Payload_Type/                            # empty (C2-only profile)
└── C2_Profiles/
    └── notion/
        ├── Dockerfile
        ├── requirements.txt
        ├── mythic/
        │   └── c2_functions/
        │       └── Notion.py                # Docker entry point + C2Profile class
        └── c2_code/
            ├── main.py                      # Poll loop + Mythic forwarding
            ├── notion_client.py             # Notion API wrapper
            └── config.json.example          # Config template
```
