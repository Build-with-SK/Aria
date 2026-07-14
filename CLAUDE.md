# Trading Intelligence System — Claude Instructions

## User's Obsidian Vault (knowledge base)

The user's personal knowledge base is an Obsidian vault at:

```
C:\Users\sound\Documents\DigitalBrain
```

It is plain markdown — **read it directly with file tools** (Read/Grep/Glob); no MCP server is needed or wanted. Key locations:

- `00 - System\Master Profile\MY_AI_CONTEXT.md` — who the user is, skill levels, tone preferences. Read this when personal context matters.
- `00 - System\Master Index.md` — top-level map of the vault (reorganized 2026-07-06)
- `01 - Trading\` — trading knowledge, strategies, daily NSE scans, TIS reports
- `02 - Finance\` — finance research, business notes, market Wiki
- `03 - Content\`, `04 - Creative\`, `05 - Learning\`, `06 - Personal\` — content, music/film, coding/engineering, cooking/gaming/daily notes
- `_Archive\` — superseded material (older TIS snapshot, old conversations)
- Skip `.obsidian`, `.trash`, `.agent`, `.makemd`, `.space` (app internals)

When a task touches the user's skills, strategies, or personal context, search the vault first (Grep for keywords) instead of asking.

## ARIA's vault access

ARIA (the app) reads the same vault via `src/brain/vault.py` — a ChromaDB semantic index (collection `obsidian_vault`, stored in `data/brain_memory/chromadb`). Endpoints: `/api/vault/status`, `/api/vault/reindex`, `/api/vault/search?q=`, `/api/vault/note?path=`. Vault knowledge is auto-injected into both cloud and local chat and into the brain daemon's RECALL step. If the user moves their vault, update `data/vault_config.json`.

## Project conventions

- Windows: use `pathlib.Path`, venv python at `venv\Scripts\python.exe`
- Ollama calls: `urllib.request` only (no requests/httpx)
- Backend: only ADD endpoints to `backend/main.py`, never remove/rename; lazy-import heavy modules inside functions
- Frontend: inline styles only, Bloomberg-terminal aesthetic (`var(--mono)`, `var(--orange)`, `var(--green)`); no new npm packages
- The brain proposes, the human approves: nothing may call `approve_and_execute` autonomously
