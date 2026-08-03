"""
src/brain/vault.py
==================
OBSIDIAN VAULT BRIDGE — no MCP server, no plugins.

An Obsidian vault is just a folder of markdown files. This module indexes
the vault into ChromaDB (same engine as the brain's long-term memory,
separate collection) so ARIA can semantically search the user's skills
and knowledge and inject relevant notes into chat and reasoning cycles.

Vault path is configured in data/vault_config.json.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
CONFIG_FILE = ROOT / "data" / "vault_config.json"
MANIFEST_FILE = ROOT / "data" / "brain_memory" / "vault_manifest.json"
DB_PATH = ROOT / "data" / "brain_memory" / "chromadb"

# Resolution order: data/vault_config.json → ARIA_VAULT_PATH → a DigitalBrain
# folder beside this repo. No absolute path from any one machine is baked in;
# that leaked a username into the source and broke for every other user.
DEFAULT_VAULT = os.environ.get("ARIA_VAULT_PATH") or str(ROOT.parent / "DigitalBrain")

CHUNK_SIZE = 1200      # characters per chunk, split on paragraph boundaries
MAX_FILE_BYTES = 512_000


def get_vault_path() -> Path:
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return Path(cfg.get("vault_path", DEFAULT_VAULT))
        except Exception:
            pass
    return Path(DEFAULT_VAULT)


def set_vault_path(path: str):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({"vault_path": path}, indent=2), encoding="utf-8")


def _chunk(text: str) -> list[str]:
    """Split note text into ~CHUNK_SIZE chunks on paragraph boundaries."""
    paras = re.split(r"\n\s*\n", text)
    chunks, buf = [], ""
    for p in paras:
        p = p.strip()
        if not p:
            continue
        if len(buf) + len(p) + 2 > CHUNK_SIZE and buf:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks


class VaultIndex:
    """Semantic index over the Obsidian vault's markdown files."""

    def __init__(self, vault_path: Path | None = None):
        import chromadb
        from sentence_transformers import SentenceTransformer
        self.vault = vault_path or get_vault_path()
        DB_PATH.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(DB_PATH))
        self.collection = self.client.get_or_create_collection("obsidian_vault")
        self.embedder = SentenceTransformer("all-MiniLM-L6-v2")

    # ── indexing ─────────────────────────────────────────────────────────

    def _iter_notes(self):
        """Yield (relpath, Path) for every indexable .md file in the vault."""
        if not self.vault.exists():
            return
        for p in self.vault.rglob("*.md"):
            rel = p.relative_to(self.vault)
            # skip hidden/system dirs (.obsidian, .trash, .agent, .makemd, .space)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
            yield str(rel).replace("\\", "/"), p

    def reindex(self, force: bool = False) -> dict:
        """
        Incremental re-index: embed new/changed notes, remove deleted ones.
        Returns counts. force=True re-embeds everything.
        """
        manifest = self._load_manifest() if not force else {}
        seen, added, updated = set(), 0, 0

        for rel, path in self._iter_notes():
            seen.add(rel)
            mtime = path.stat().st_mtime
            if not force and manifest.get(rel) == mtime:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                logger.warning(f"Vault: cannot read {rel}: {e}")
                continue
            self._remove_note(rel)
            chunks = _chunk(text)
            if chunks:
                title = path.stem
                ids = [f"{rel}#{i}" for i in range(len(chunks))]
                docs = [f"[{title}]\n{c}" for c in chunks]
                embeddings = self.embedder.encode(docs).tolist()
                self.collection.upsert(
                    ids=ids,
                    embeddings=embeddings,
                    documents=docs,
                    metadatas=[{"path": rel, "title": title,
                                "folder": rel.split("/")[0] if "/" in rel else "",
                                "chunk": i, "mtime": mtime}
                               for i in range(len(chunks))],
                )
            if rel in manifest:
                updated += 1
            else:
                added += 1
            manifest[rel] = mtime

        removed = [rel for rel in list(manifest) if rel not in seen]
        for rel in removed:
            self._remove_note(rel)
            del manifest[rel]

        self._save_manifest(manifest)
        result = {"notes_indexed": len(manifest), "added": added,
                  "updated": updated, "removed": len(removed),
                  "chunks": self.collection.count(),
                  "vault_path": str(self.vault)}
        logger.info(f"Vault reindex: {result}")
        return result

    def _remove_note(self, rel: str):
        try:
            self.collection.delete(where={"path": rel})
        except Exception:
            pass

    # ── retrieval ────────────────────────────────────────────────────────

    def search(self, query: str, n: int = 5) -> list[dict]:
        """Semantic search over vault notes. Returns snippets with paths."""
        if self.collection.count() == 0:
            return []
        embedding = self.embedder.encode(query).tolist()
        res = self.collection.query(query_embeddings=[embedding],
                                    n_results=min(n, self.collection.count()))
        out = []
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for doc, meta, dist in zip(docs, metas, dists):
            meta = meta or {}
            out.append({
                "path": meta.get("path", ""),
                "title": meta.get("title", ""),
                "folder": meta.get("folder", ""),
                "snippet": doc[:600],
                "distance": round(dist, 4) if dist is not None else None,
            })
        return out

    def context_for(self, query: str, n: int = 3, max_chars: int = 1500) -> str:
        """Formatted vault knowledge block for prompt injection. '' if nothing."""
        hits = self.search(query, n=n)
        if not hits:
            return ""
        lines, used = [], 0
        for h in hits:
            entry = f"• [{h['path']}]\n{h['snippet']}"
            if used + len(entry) > max_chars:
                entry = entry[: max_chars - used]
            lines.append(entry)
            used += len(entry)
            if used >= max_chars:
                break
        return ("── FROM THE USER'S OBSIDIAN VAULT (their own notes/knowledge) ──\n"
                + "\n\n".join(lines)
                + "\n──────────────────────────────────────────────────────────────")

    def read_note(self, rel_path: str) -> str | None:
        """Full text of one note, by vault-relative path.

        Containment is checked with `is_relative_to`, not a string prefix. A
        prefix test passes for a SIBLING directory whose name merely starts
        with the vault's — "…/DigitalBrain-backup" satisfies
        startswith("…/DigitalBrain") — which would let a crafted path escape
        the vault and read arbitrary files through the API.
        """
        root = self.vault.resolve()
        try:
            path = (root / rel_path).resolve()
        except (OSError, ValueError):
            return None
        if not path.is_relative_to(root) or not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="ignore")

    def stats(self) -> dict:
        manifest = self._load_manifest()
        return {
            "vault_path": str(self.vault),
            "vault_exists": self.vault.exists(),
            "notes_indexed": len(manifest),
            "chunks": self.collection.count(),
        }

    # ── manifest ─────────────────────────────────────────────────────────

    def _load_manifest(self) -> dict:
        if MANIFEST_FILE.exists():
            try:
                return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_manifest(self, manifest: dict):
        MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# ── module singleton ─────────────────────────────────────────────────────

_vault: VaultIndex | None = None


def get_vault() -> VaultIndex:
    """Singleton VaultIndex; indexes lazily on first access if empty."""
    global _vault
    if _vault is None:
        _vault = VaultIndex()
        if _vault.collection.count() == 0:
            _vault.reindex()
    return _vault


def peek_vault() -> VaultIndex | None:
    return _vault
