"""Install and exercise the TestPyPI wheel verified against one CI artifact."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import BadZipFile, ZipFile

from scripts import release_status

MAX_WHEEL_BYTES = 20 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT = 60.0
SOCKET_TIMEOUT = 15.0
COMMAND_TIMEOUT = 300
DEMO_TIMEOUT = 3600
SMOKE = Path(__file__).with_name("smoke.py").resolve()

# Preserve platform and terminal support without inheriting tool credentials or
# package-manager/Python settings. Flexi's own paths are set below on every OS.
PLATFORM_ENVIRONMENT = frozenset(
    {
        "PATH",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "PYENV_ROOT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "SYSTEMDRIVE",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "COLORTERM",
        "NO_COLOR",
        "TZ",
    }
)

INSTALLED_CHECK = """
import importlib.metadata
import json
import pathlib
import sys
import flexi
version, wheel_uri, digest = sys.argv[1:]
distribution = importlib.metadata.distribution("flexi")
assert distribution.version == version, "Installed version differs from the release"
installed = pathlib.Path(flexi.__file__).resolve()
assert installed.is_relative_to(pathlib.Path(sys.prefix).resolve()), \\
    "Imported flexi outside the probe venv"
origin = json.loads(distribution.read_text("direct_url.json") or "{}")
assert origin.get("url") == wheel_uri, "Installed wheel has an unexpected origin"
hashes = origin.get("archive_info", {}).get("hashes", {})
# uv may omit archive hashes for local wheels; bytes were checked before install.
assert hashes.get("sha256", digest) == digest, "Installed wheel has an unexpected hash"
"""


class ProbeError(Exception):
    """The staged release could not be verified or exercised in isolation."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        message = (
            "TestPyPI wheel download redirected; refusing an unverified destination"
        )
        raise ProbeError(message)


def wheel_url(version: str, digest: str) -> str:
    document = release_status.request_json(
        f"https://test.pypi.org/pypi/flexi/{version}/json"
    )
    filename = f"flexi-{version}-py3-none-any.whl"
    entries = document.get("urls") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        message = "TestPyPI did not return distribution metadata"
        raise ProbeError(message)
    wheels = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("filename") == filename
    ]
    if len(wheels) != 1:
        message = "TestPyPI must contain exactly one wheel for this release"
        raise ProbeError(message)
    wheel = wheels[0]
    hashes = wheel.get("digests")
    if not isinstance(hashes, dict) or hashes.get("sha256") != digest:
        message = (
            "TestPyPI wheel differs from the tested artifact; use its original run"
        )
        raise ProbeError(message)
    url = wheel.get("url")
    if not isinstance(url, str):
        message = "TestPyPI wheel has no download URL"
        raise ProbeError(message)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "test-files.pythonhosted.org"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith(f"/{filename}")
        or any(character.isspace() for character in url)
    ):
        message = "TestPyPI wheel has an unexpected download URL"
        raise ProbeError(message)
    return url


def download_wheel(url: str, digest: str, destination: Path) -> None:
    """Keep network work in memory so a timed-out thread cannot write after cleanup."""
    results: list[bytes] = []
    errors: list[Exception] = []

    def fetch() -> None:
        try:
            opener = build_opener(NoRedirect())
            request = Request(url, headers={"Accept-Encoding": "identity"})  # noqa: S310
            with opener.open(request, timeout=SOCKET_TIMEOUT) as response:
                body = response.read(MAX_WHEEL_BYTES + 1)
            results.append(body)
        except (OSError, ValueError, ProbeError) as error:
            errors.append(error)

    worker = Thread(target=fetch, daemon=True)
    worker.start()
    worker.join(DOWNLOAD_TIMEOUT)
    if worker.is_alive():
        message = "TestPyPI wheel download timed out; retry the probe"
        raise ProbeError(message)
    if errors:
        if isinstance(errors[0], ProbeError):
            raise errors[0]
        message = "Could not download the TestPyPI wheel; check registry connectivity"
        raise ProbeError(message) from errors[0]
    body = results[0]
    if len(body) > MAX_WHEEL_BYTES:
        message = "TestPyPI wheel exceeds the probe's download size limit"
        raise ProbeError(message)
    if hashlib.sha256(body).hexdigest() != digest:
        message = "Downloaded TestPyPI wheel differs from the tested artifact"
        raise ProbeError(message)
    destination.write_bytes(body)


def check_metadata(wheel: Path, version: str) -> None:
    expected = f"flexi-{version}.dist-info/METADATA"
    with ZipFile(wheel) as archive:
        metadata = [entry for entry in archive.infolist() if entry.filename == expected]
        if len(metadata) != 1 or metadata[0].file_size > MAX_METADATA_BYTES:
            message = "Wheel has missing, duplicate or oversized release metadata"
            raise ProbeError(message)
        with archive.open(metadata[0]) as stream:
            body = stream.read(MAX_METADATA_BYTES + 1)
    if len(body) > MAX_METADATA_BYTES:
        message = "Wheel metadata exceeds the probe's size limit"
        raise ProbeError(message)
    document = BytesParser(policy=policy.default).parsebytes(body)
    if document.get("Name") != "flexi" or document.get("Version") != version:
        message = "Wheel metadata does not identify the requested Flexi release"
        raise ProbeError(message)
    if any("@" in str(value) for value in document.get_all("Requires-Dist", [])):
        message = (
            "Wheel uses direct-URL dependencies; the probe permits PyPI packages only"
        )
        raise ProbeError(message)


def environment(root: Path) -> dict[str, str]:
    values = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in PLATFORM_ENVIRONMENT
    }
    temporary = root / "tmp"
    temporary.mkdir()
    values.update(
        {
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "TMPDIR": str(temporary),
            "TMP": str(temporary),
            "TEMP": str(temporary),
            "NETRC": os.devnull,
        }
    )
    return values


def run(
    command: list[str],
    *,
    root: Path,
    env: dict[str, str],
    operation: str,
    interactive: bool = False,
) -> None:
    print(f"{operation}...", flush=True)
    try:
        subprocess.run(  # noqa: S603 - fixed tools, explicit paths and argument lists
            command,
            cwd=root,
            env=env,
            check=True,
            capture_output=not interactive,
            encoding="utf-8",
            errors="replace",
            timeout=DEMO_TIMEOUT if interactive else COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired as error:
        message = f"{operation} timed out; retry the probe"
        raise ProbeError(message) from error
    except subprocess.CalledProcessError as error:
        message = (
            f"{operation} failed (exit {error.returncode}); the probe did not pass"
        )
        diagnostic = error.stderr or error.stdout
        if isinstance(diagnostic, str) and diagnostic.strip():
            readable = "".join(
                character
                for character in diagnostic[-4000:]
                if character.isprintable() or character in "\n\t"
            )
            message += f"\n{readable.strip()}"
        raise ProbeError(message) from error
    except OSError as error:
        message = (
            f"Could not start {operation.lower()}; check the Python and uv installation"
        )
        raise ProbeError(message) from error


def probe_release(version: str, artifact_dir: Path, *, demo: bool = False) -> None:
    if demo and not (sys.stdin.isatty() and sys.stdout.isatty()):
        message = "The demo needs an interactive terminal; omit --demo for a smoke test"
        raise ProbeError(message)
    uv = shutil.which("uv")
    if uv is None:
        message = "Install uv before running the local release probe"
        raise ProbeError(message)
    root: Path | None = None
    try:
        release_status.validate_version(version)
        release_status.verify_artifacts(
            artifact_dir,
            version,
            registry=release_status.Registry.TESTPYPI,
            complete=True,
        )
        filename = f"flexi-{version}-py3-none-any.whl"
        with (artifact_dir / filename).open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        url = wheel_url(version, digest)
        with tempfile.TemporaryDirectory(prefix="flexi-release-probe-") as temporary:
            root = Path(temporary).resolve()
            env = environment(root)
            wheel = root / filename
            download_wheel(url, digest, wheel)
            check_metadata(wheel, version)
            venv = root / "venv"
            python = venv / (
                "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
            )
            console = venv / (
                "Scripts/flexi.exe" if sys.platform == "win32" else "bin/flexi"
            )
            uv_command = [uv, "--no-config", "--cache-dir", str(root / "uv-cache")]
            commands = [
                (
                    [
                        *uv_command,
                        "venv",
                        "--python",
                        sys.executable,
                        "--no-python-downloads",
                        str(venv),
                    ],
                    "Creating the temporary environment",
                ),
                (
                    [
                        *uv_command,
                        "pip",
                        "install",
                        "--python",
                        str(python),
                        "--no-build",
                        "--index-url",
                        "https://pypi.org/simple/",
                        "--keyring-provider",
                        "disabled",
                        str(wheel),
                    ],
                    "Installing the staged wheel and its PyPI dependencies",
                ),
                (
                    [*uv_command, "pip", "check", "--python", str(python)],
                    "Checking installed dependencies",
                ),
                (
                    [
                        str(python),
                        "-I",
                        "-c",
                        INSTALLED_CHECK,
                        version,
                        wheel.as_uri(),
                        digest,
                    ],
                    "Checking the installed wheel's identity",
                ),
                (
                    [str(console), "--version"],
                    "Checking the packaged version command",
                ),
                (
                    [str(console), "--help"],
                    "Checking packaged command help",
                ),
                ([str(python), "-I", str(SMOKE)], "Booting the installed application"),
            ]
            for command, operation in commands:
                run(command, root=root, env=env, operation=operation)
            if demo:
                run(
                    [str(python), "-I", "-m", "flexi", "--demo"],
                    root=root,
                    env=env,
                    operation="Running the demo",
                    interactive=True,
                )
    except release_status.ReleaseError as error:
        raise ProbeError(str(error)) from error
    except (OSError, ValueError, BadZipFile) as error:
        location = f" at {root}" if root is not None else ""
        message = (
            f"Could not read or clean probe files{location}; "
            "close any process using them and retry"
        )
        raise ProbeError(message) from error
