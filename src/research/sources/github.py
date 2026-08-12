"""
src/research/sources/github.py
==============================
GitHub repos, issues and PRs through the official `gh` CLI.

`gh` is the one backend here with a real API contract behind it: it is
first-party, authenticated, rate-limit aware, and it will not break because
someone changed a CSS class. When `gh` is absent the source falls back to
reading the page through WebSource rather than failing.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from urllib.parse import urlsplit

from ..base import Document, Source, SourceError, probe_command
from ..url import host_matches, normalize_public_url

TIMEOUT = 45


def _repo_path(url: str) -> tuple[str, str, str]:
    """Split a github.com URL into (owner/repo, kind, number).

    kind is "repo", "issue" or "pr"; number is "" for a plain repo.
    """
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if len(parts) < 2:
        raise SourceError(f"not a repository URL: {url}")
    slug = f"{parts[0]}/{parts[1]}"
    if len(parts) >= 4 and parts[2] in ("issues", "pull"):
        return slug, ("issue" if parts[2] == "issues" else "pr"), parts[3]
    return slug, "repo", ""


class GitHubSource(Source):
    name = "github"
    description = "GitHub repositories, issues and pull requests via gh CLI"
    backends = ["gh", "jina"]
    zero_config = False

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "github.com")

    def available(self) -> tuple[bool, str]:
        if shutil.which("gh") and probe_command(["gh", "--version"], timeout=15):
            return True, "gh"
        return True, "jina (gh not installed — falling back to page read)"

    def _gh(self, argv: list[str]) -> str:
        try:
            proc = subprocess.run(
                ["gh"] + argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=TIMEOUT,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SourceError(f"gh {' '.join(argv)} failed: {exc}") from exc
        if proc.returncode != 0:
            raise SourceError(
                f"gh {' '.join(argv)} exited {proc.returncode}: "
                f"{(proc.stderr or '').strip()[:300]}"
            )
        return proc.stdout

    def fetch(self, url: str) -> Document:
        clean = normalize_public_url(url)

        if not shutil.which("gh"):
            from .web import WebSource
            doc = WebSource().fetch(clean)
            doc.source = self.name
            doc.backend = "jina"
            return doc

        slug, kind, number = _repo_path(clean)

        if kind == "repo":
            raw = self._gh([
                "repo", "view", slug,
                "--json", "name,owner,description,stargazerCount,forkCount,"
                          "primaryLanguage,licenseInfo,pushedAt,createdAt,url",
            ])
            info = json.loads(raw or "{}")
            readme = ""
            try:
                readme = self._gh(["api", f"repos/{slug}/readme", "-H",
                                   "Accept: application/vnd.github.raw"])
            except SourceError:
                pass          # a repo without a README is still a valid answer

            lang = (info.get("primaryLanguage") or {}).get("name", "")
            lic = (info.get("licenseInfo") or {}).get("spdxId", "")
            header = (
                f"# {slug}\n\n"
                f"{info.get('description', '')}\n\n"
                f"- stars: {info.get('stargazerCount')}\n"
                f"- forks: {info.get('forkCount')}\n"
                f"- language: {lang}\n"
                f"- license: {lic}\n"
                f"- created: {info.get('createdAt', '')}\n"
                f"- last push: {info.get('pushedAt', '')}\n"
            )
            return Document(
                url=clean,
                title=slug,
                text=header + "\n" + readme,
                source=self.name,
                backend="gh",
                meta={
                    "repo": slug,
                    "kind": "repo",
                    "stars": info.get("stargazerCount"),
                    "forks": info.get("forkCount"),
                    "license": lic,
                    "pushed_at": info.get("pushedAt", ""),
                },
            )

        command = "issue" if kind == "issue" else "pr"
        raw = self._gh([
            command, "view", number, "--repo", slug,
            "--json", "title,body,state,author,createdAt,url,comments",
        ])
        info = json.loads(raw or "{}")
        comments = "\n\n".join(
            f"**{(c.get('author') or {}).get('login', '?')}**: {c.get('body', '')}"
            for c in (info.get("comments") or [])
        )
        return Document(
            url=clean,
            title=f"{slug}#{number}: {info.get('title', '')}",
            text=f"# {info.get('title', '')}\n\n{info.get('body', '')}\n\n{comments}",
            source=self.name,
            backend="gh",
            meta={
                "repo": slug,
                "kind": kind,
                "number": number,
                "state": info.get("state", ""),
                "author": (info.get("author") or {}).get("login", ""),
                "created_at": info.get("createdAt", ""),
            },
        )
