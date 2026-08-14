"""Checking GitHub for a newer version, and fetching it.

Deliberately free of Qt and of any third-party HTTP library. `urllib` is in the
standard library, so this adds nothing to the bundle, and keeping it free of Qt
means the version arithmetic — the part that is easy to get quietly wrong — can
be tested without a display.

Nothing here installs anything. It answers "is there something newer?" and, if
asked, downloads the file; running it is the caller's decision and the caller's
code. That separation is not fussiness: an updater that can silently fetch and
execute code is a thing worth being able to read in one sitting.

**Set `GITHUB_OWNER` below** to the account hosting the releases. Until it is
set the app says update checking is not configured rather than failing in a way
that looks like a network problem.
"""
from __future__ import annotations

import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# --- where releases live ----------------------------------------------------
GITHUB_OWNER = "harpe119"
# The repository *slug*, exactly as it appears in the URL — which is not
# necessarily the folder it is checked out into. This is github.com/harpe119/
# Financing-With-Doug, hyphens and all; the local directory and the installer
# both call it FinancingWithDoug, and getting the two confused reads as "the
# repository is private" because GitHub 404s an unknown name the same way.
GITHUB_REPO = "Financing-With-Doug"

API_ROOT = "https://api.github.com"
# Only these hosts are ever fetched from. GitHub redirects release downloads to
# its object store, so both have to be allowed; anything else is refused rather
# than followed, because "download and run this" is exactly the instruction an
# updater must not take from an untrusted redirect.
ALLOWED_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com")
TIMEOUT = 10.0
USER_AGENT = "FinancingWithDoug-updater"
CHECKSUM_ASSET = "SHA256SUMS.txt"


def configured() -> bool:
    return bool(GITHUB_OWNER and GITHUB_REPO)


# --- version arithmetic -----------------------------------------------------
_VER_RE = re.compile(r"(\d+(?:\.\d+)*)")


def parse_version(s: str) -> tuple[int, ...]:
    """"v1.15.1" → (1, 15, 1). Anything unparseable → ()."""
    m = _VER_RE.search(str(s or ""))
    if not m:
        return ()
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    """Is `candidate` a later version than `current`?

    Padded comparison, so 1.16 beats 1.15.9 and 1.15.1 beats 1.15. An
    unparseable candidate is never newer — the failure mode of a malformed tag
    should be "no update offered", never "offer everything".
    """
    a, b = parse_version(candidate), parse_version(current)
    if not a:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


# --- what came back ---------------------------------------------------------
@dataclass
class Release:
    version: str                   # the tag, minus any leading v
    name: str
    notes: str                     # the release body, as written on GitHub
    page_url: str                  # where a human should be sent
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    checksums_url: str = ""
    published: date | None = None

    @property
    def has_installer(self) -> bool:
        return bool(self.asset_url)

    @property
    def size_mb(self) -> float:
        return self.asset_size / 1_048_576.0


class UpdateError(RuntimeError):
    """Anything that stopped a check or a download, in words."""


def _host_ok(url: str) -> bool:
    from urllib.parse import urlparse
    p = urlparse(url)
    return p.scheme == "https" and p.hostname in ALLOWED_HOSTS


def _open(url: str, timeout: float = TIMEOUT):
    if not _host_ok(url):
        raise UpdateError(f"Refusing to fetch from an unexpected address: {url}")
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    # An explicit default context, so a frozen build that has not inherited the
    # system store still verifies certificates rather than silently not.
    return urllib.request.urlopen(req, timeout=timeout,
                                  context=ssl.create_default_context())


def latest_release(owner: str | None = None, repo: str | None = None,
                   timeout: float = TIMEOUT) -> Release | None:
    """The newest published release, or None if there are none.

    `/releases/latest` excludes drafts and pre-releases, which is what makes
    "tag a release when it is ready" a safe publishing workflow.
    """
    owner, repo = owner or GITHUB_OWNER, repo or GITHUB_REPO
    if not (owner and repo):
        raise UpdateError(
            "Update checking is not set up in this build — GITHUB_OWNER is "
            "empty in finfore/update.py.")
    url = f"{API_ROOT}/repos/{owner}/{repo}/releases/latest"
    try:
        with _open(url, timeout) as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # GitHub answers 404 both for "this repository has no releases yet"
            # and for "this repository is not visible to you", and the
            # difference is the whole ball game. Reporting a private repo as
            # "no releases" means every installed copy is told it is up to date
            # for ever, silently, and nobody finds out until somebody mentions
            # in passing that they are still on 1.2. One extra request settles
            # it, and only on the path that is already going wrong.
            if repo_visible(owner, repo, timeout):
                return None
            raise UpdateError(
                f"Can't see github.com/{owner}/{repo}.\n\n"
                f"If the repository is private, an anonymous update check "
                f"cannot reach it — releases have to be somewhere public for "
                f"this to work. docs/releasing.md covers the options.") from e
        if e.code in (403, 429):
            raise UpdateError(
                "GitHub is rate-limiting this connection (it allows 60 "
                "anonymous requests an hour). Try again later.") from e
        raise UpdateError(f"GitHub said {e.code} {e.reason}.") from e
    except urllib.error.URLError as e:
        raise UpdateError(f"Couldn't reach GitHub: {e.reason}") from e
    except (ValueError, TimeoutError, OSError) as e:
        raise UpdateError(f"Couldn't read GitHub's reply: {e}") from e
    return _release_from(data)


def repo_visible(owner: str, repo: str, timeout: float = TIMEOUT) -> bool:
    """Can this repository be seen without credentials?

    Used only to turn one ambiguous 404 into two distinguishable answers.
    Anything other than a clean 200 counts as "no", including a network
    failure — the caller is already on an error path and a maybe is no use to
    it.
    """
    try:
        with _open(f"{API_ROOT}/repos/{owner}/{repo}", timeout) as fh:
            fh.read(1)
        return True
    except Exception:
        return False


def _release_from(data: dict) -> Release:
    tag = str(data.get("tag_name") or "")
    rel = Release(
        version=tag.lstrip("vV"),
        name=str(data.get("name") or tag),
        notes=str(data.get("body") or ""),
        page_url=str(data.get("html_url") or ""),
    )
    when = data.get("published_at")
    if when:
        try:
            rel.published = datetime.fromisoformat(
                str(when).replace("Z", "+00:00")).date()
        except ValueError:
            pass
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if not url or not _host_ok(url):
            continue
        if name == CHECKSUM_ASSET:
            rel.checksums_url = url
        elif name.lower().endswith(".exe") and not rel.asset_url:
            rel.asset_name = name
            rel.asset_url = url
            rel.asset_size = int(asset.get("size") or 0)
    return rel


def check(current_version: str, owner: str | None = None,
          repo: str | None = None, timeout: float = TIMEOUT) -> Release | None:
    """The newest release *if* it is newer than what is running, else None."""
    rel = latest_release(owner, repo, timeout)
    if rel is None or not is_newer(rel.version, current_version):
        return None
    return rel


# --- fetching ---------------------------------------------------------------
def download(release: Release, dest_dir: Path, progress=None,
             timeout: float = TIMEOUT) -> Path:
    """Fetch the installer into `dest_dir` and return the path.

    `progress(done_bytes, total_bytes)` may return False to cancel, which
    deletes the part-file — a half-downloaded installer left lying in Temp is
    the kind of thing somebody double-clicks a month later.

    The checksum is verified when the release publishes one. Over HTTPS this is
    guarding against a truncated or corrupted download rather than against an
    attacker, which is the failure that actually happens.
    """
    if not release.has_installer:
        raise UpdateError("That release has no installer attached to it.")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / release.asset_name

    want = _expected_sha256(release, timeout) if release.checksums_url else None
    digest = hashlib.sha256()
    done = 0
    try:
        with _open(release.asset_url, timeout) as fh, open(target, "wb") as out:
            total = int(fh.headers.get("Content-Length")
                        or release.asset_size or 0)
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None and progress(done, total) is False:
                    raise UpdateError("cancelled")
    except UpdateError:
        target.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        target.unlink(missing_ok=True)
        raise UpdateError(f"The download failed: {e}") from e

    if want and digest.hexdigest().lower() != want:
        target.unlink(missing_ok=True)
        raise UpdateError(
            "The downloaded file does not match the checksum published with "
            "the release, so it arrived damaged. Nothing was installed.")
    return target


def _expected_sha256(release: Release, timeout: float) -> str | None:
    """The published hash for this asset, from a `sha256  filename` list."""
    try:
        with _open(release.checksums_url, timeout) as fh:
            text = fh.read().decode("utf-8", "replace")
    except Exception:
        return None                # no checksum is not a reason to refuse
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == release.asset_name:
            return parts[0].strip().lower()
    return None
