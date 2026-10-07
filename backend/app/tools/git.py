"""Read-only Git investigation tools.

Answer "what code changed?" for a service: commits, their metadata and the diff
between two deployed commits, limited to the service's directory in the repo.

Where the history comes from: scripts/sync-git-mirror.sh copies a `git bundle`
of the repository into the backend pod. sync() fetches it into a private bare
repository (the cache), and every query reads that cache. Nothing is pushed,
checked out or executed from the repository.

Safety:
  - git runs as a fixed argument list (no shell), so nothing in a ref can be
    interpreted as a command.
  - Refs must be commit SHAs (they come from Deployment annotations, i.e. from
    the cluster) and are passed after --end-of-options, so a value like
    "--output=/etc/x" can never become a git option.
  - A service's path comes from configuration, never from the request.
"""

import os
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from app.models.evidence import CommitInfo, FileChange, GitDiff
from app.tools.errors import InvalidTargetError

SHA = re.compile(r"^[0-9a-f]{7,40}$")
MAX_PATCH_CHARS = 30_000
MAX_BODY_CHARS = 2_000
MAX_COMMITS = 50
# Field and record separators for `git log --format`.
FS, RS = "\x1f", "\x1e"
LOG_FORMAT = f"%H{FS}%an{FS}%aI{FS}%cI{FS}%s{FS}%b{RS}"


class GitError(RuntimeError):
    pass


class GitTools:
    def __init__(self, source: str, cache_dir: str, service_paths: dict[str, str],
                 timeout_seconds: float = 20.0) -> None:
        self.source = source  # bundle file (in the cluster) or repository path/URL (locally, tests)
        self.cache_dir = cache_dir
        self.service_paths = service_paths
        self.timeout = timeout_seconds
        self._sync_lock = threading.Lock()
        self._synced_source_mtime: float | None = None

    # ---------------------------------------------------------- plumbing ---

    def _git(self, *args: str) -> str:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            result = subprocess.run(
                ["git", "--git-dir", self.cache_dir, *args],
                capture_output=True, text=True, timeout=self.timeout, env=env, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {args[0]} timed out after {self.timeout}s") from exc
        if result.returncode != 0:
            raise GitError(f"git {args[0]} failed: {result.stderr.strip()[:500]}")
        return result.stdout

    def sync(self) -> None:
        """Fetch all branches and tags from the source into the cache.

        A bundle file is only re-read when it changed. Concurrent requests run
        in a thread pool, so fetches are serialized with a lock.
        """
        with self._sync_lock:
            source_path = Path(self.source)
            if self.source.endswith(".bundle") and not source_path.is_file():
                raise GitError(f"no git history at {self.source}: run scripts/sync-git-mirror.sh")
            mtime = source_path.stat().st_mtime if source_path.is_file() else None
            if mtime is not None and mtime == self._synced_source_mtime:
                return
            if not Path(self.cache_dir, "HEAD").exists():
                Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
                self._git("init", "--quiet", "--bare")
            self._git("fetch", "--quiet", "--force", "--prune", self.source,
                      "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")
            self._synced_source_mtime = mtime

    def path_for(self, service: str) -> str:
        path = self.service_paths.get(service)
        if path is None:
            raise InvalidTargetError(
                f"no source path configured for service {service!r} (see GIT_SERVICE_PATHS)")
        return path

    def resolve(self, sha: str) -> str:
        """Full SHA of a commit. Raises InvalidTargetError for anything that isn't a SHA."""
        if not SHA.match(sha):
            raise InvalidTargetError(f"not a commit SHA: {sha!r}")
        return self._git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{sha}^{{commit}}").strip()

    def _tags_by_commit(self) -> dict[str, list[str]]:
        tags: dict[str, list[str]] = {}
        # %(*objectname) is the commit an annotated tag points to; empty for lightweight tags.
        out = self._git("for-each-ref", "refs/tags", f"--format=%(objectname){FS}%(*objectname){FS}%(refname:short)")
        for line in out.splitlines():
            obj, peeled, name = line.split(FS)
            tags.setdefault(peeled or obj, []).append(name)
        return tags

    def _files(self, from_sha: str | None, to_sha: str, path: str) -> list[FileChange]:
        """Files changed under `path` between two commits, or in one commit (from_sha=None)."""
        if from_sha:
            cmd, revs = ["diff", "--no-color", "--no-ext-diff"], [from_sha, to_sha]
        else:
            # diff-tree --root also works for the first commit, which has no parent.
            cmd, revs = ["diff-tree", "--no-commit-id", "-r", "--root"], [to_sha]
        numstat = self._git(*cmd, "--numstat", "--end-of-options", *revs, "--", path)
        status = self._git(*cmd, "--name-status", "--end-of-options", *revs, "--", path)
        counts: dict[str, tuple[int | None, int | None]] = {}
        for line in numstat.splitlines():
            added, deleted, name = line.split("\t", 2)
            counts[name] = (None if added == "-" else int(added), None if deleted == "-" else int(deleted))
        files = []
        for line in status.splitlines():
            parts = line.split("\t")
            name = parts[-1]  # renames: "R100<TAB>old<TAB>new"
            added, deleted = counts.get(name, (None, None))
            files.append(FileChange(path=name, status=parts[0][0], additions=added, deletions=deleted))
        return files

    def _log(self, rev: str, path: str, limit: int, only_touching_path: bool = True) -> list[CommitInfo]:
        """Commits for `rev` (a SHA or "A..B"), newest first; `files` limited to `path`."""
        pathspec = ["--", path] if only_touching_path else []
        out = self._git("log", f"--max-count={limit}", f"--format={LOG_FORMAT}",
                        "--end-of-options", rev, *pathspec)
        tags = self._tags_by_commit()
        commits = []
        for record in out.split(RS):
            record = record.strip("\n")
            if not record:
                continue
            sha, author, authored, committed, subject, body = record.split(FS)
            commits.append(CommitInfo(
                sha=sha, short_sha=sha[:7], author=author,
                authored_at=datetime.fromisoformat(authored),
                committed_at=datetime.fromisoformat(committed),
                subject=subject, body=body.strip()[:MAX_BODY_CHARS],
                tags=sorted(tags.get(sha, [])),
                files=self._files(None, sha, path),
            ))
        return commits

    # ------------------------------------------------------------- tools ---

    def get_commit(self, service: str, sha: str) -> CommitInfo:
        """One commit, with only the files under the service's path."""
        return self._log(self.resolve(sha), self.path_for(service), limit=1, only_touching_path=False)[0]

    def get_recent_commits(self, service: str, until_sha: str, limit: int = 10) -> list[CommitInfo]:
        """The latest commits that touched the service, up to and including until_sha. Newest first."""
        return self._log(self.resolve(until_sha), self.path_for(service), limit=min(limit, MAX_COMMITS))

    def get_commits_between(self, service: str, from_sha: str, to_sha: str) -> list[CommitInfo]:
        """Commits that touched the service after from_sha, up to to_sha. Newest first."""
        return self._log(f"{self.resolve(from_sha)}..{self.resolve(to_sha)}", self.path_for(service),
                         limit=MAX_COMMITS)

    def get_diff(self, service: str, from_sha: str, to_sha: str) -> GitDiff:
        """The code change for the service between two commits."""
        path = self.path_for(service)
        old, new = self.resolve(from_sha), self.resolve(to_sha)
        files = self._files(old, new, path)
        patch = self._git("diff", "--no-color", "--no-ext-diff", "--no-textconv", "--unified=3",
                          "--end-of-options", old, new, "--", path)
        truncated = len(patch) > MAX_PATCH_CHARS
        return GitDiff(
            from_sha=old, to_sha=new, path=path, files=files,
            additions=sum(f.additions or 0 for f in files),
            deletions=sum(f.deletions or 0 for f in files),
            patch=patch[:MAX_PATCH_CHARS] + ("\n... (diff truncated)" if truncated else ""),
            truncated=truncated,
        )
