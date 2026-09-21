# Developer commands. See docs/TASKS.md for prerequisites and side effects.
# Python script recipes keep argument passing identical on Windows and POSIX.

set positional-arguments := true
set script-interpreter := ["uv", "run", "--no-project", "python"]

[private]
[script]
default:
    import subprocess
    raise SystemExit(subprocess.call(["just", "--list", "--unsorted"]))

# Install locked development dependencies and Git hooks.
[group('Development')]
[script]
setup:
    import subprocess
    result = subprocess.call(["uv", "sync", "--locked", "--dev"])
    if result:
        raise SystemExit(result)
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "pre-commit", "install"]))

# Run Flexi with your real data; forward arguments to the CLI.
[group('Development')]
[script]
run *args:
    import subprocess, sys
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "flexi", *sys.argv[1:]]))

# Explore the interactive demo with temporary data.
[group('Development')]
[script]
demo:
    import subprocess
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "flexi", "--demo"]))

# Start Textual development mode against your real database.
[group('Development')]
[script]
dev:
    import subprocess
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "textual", "run", "--dev", "scripts/dev.py"]))

# Watch Textual logs in a second terminal while dev is running.
[group('Development')]
[script]
console:
    import subprocess
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "textual", "console"]))

# Check the lockfile, formatting, lint, types and workflows without fixing source.
[group('Checks')]
[script]
check: lint types workflow-check
    import subprocess
    raise SystemExit(subprocess.call(["uv", "lock", "--check"]))

# Check Python and justfile formatting and Python lint.
[group('Checks')]
[script]
lint:
    import subprocess
    for command in (["uv", "run", "--locked", "ruff", "check"],
                    ["uv", "run", "--locked", "ruff", "format", "--check"],
                    ["just", "--unstable", "--fmt", "--check"]):
        result = subprocess.call(command)
        if result:
            raise SystemExit(result)

# Check strict typing for the host platform and Windows.
[group('Checks')]
[script]
types:
    import subprocess
    for args in ([], ["--platform", "win32", "--no-warn-unreachable"]):
        result = subprocess.call(["uv", "run", "--locked", "mypy", *args])
        if result:
            raise SystemExit(result)

# Apply Ruff's safe fixes and format Python and the justfile (writes source).
[group('Checks')]
[script]
fix:
    import subprocess
    results = [subprocess.call(command) for command in (
        ["uv", "run", "--locked", "ruff", "check", "--fix"],
        ["uv", "run", "--locked", "ruff", "format"],
        ["just", "--unstable", "--fmt"],
    )]
    raise SystemExit(next((result for result in results if result), 0))

# Run all Git hooks; formatter and lint hooks can modify files.
[group('Checks')]
[script]
hooks:
    import subprocess
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "pre-commit", "run", "--all-files"]))

# Audit locked dependencies for this OS using the online advisory service.
[group('Checks')]
[script]
audit:
    import subprocess, tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix="flexi-audit-") as directory:
        requirements = str(Path(directory) / "requirements.txt")
        result = subprocess.call(["uv", "export", "--locked", "--no-emit-project", "--group", "dev", "--output-file", requirements, "--quiet"])
        if result:
            raise SystemExit(result)
        result = subprocess.call(["uvx", "--from", "pip-audit==2.10.1", "pip-audit", "--disable-pip", "--no-deps", "--progress-spinner", "off", "-r", requirements])
    raise SystemExit(result)

# Validate every GitHub Actions workflow with actionlint.
[group('Checks')]
[script]
workflow-check:
    import subprocess
    from pathlib import Path
    workflows = sorted(str(path) for path in Path(".github/workflows").glob("*.yaml"))
    raise SystemExit(subprocess.call(["uvx", "--from", "actionlint-py==1.7.12.24", "actionlint", *workflows]))

# Run tests; accepts any pytest arguments, e.g. tests/domain -q.
[group('Testing')]
[script]
test *args:
    import subprocess, sys
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "pytest", *sys.argv[1:]]))

# Run tests with CI's larger property-testing budget.
[group('Testing')]
[script]
test-ci *args:
    import os, subprocess, sys
    environment = dict(os.environ, HYPOTHESIS_PROFILE="ci")
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "pytest", "--durations=15", *sys.argv[1:]], env=environment))

# Enforce full branch coverage with CI's property-testing budget.
[group('Testing')]
[script]
coverage *args:
    import os, subprocess, sys
    environment = dict(os.environ, HYPOTHESIS_PROFILE="ci")
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "pytest", "--cov", "--cov-report=term-missing", *sys.argv[1:]], env=environment))

# Reproduce slow-runner callback ordering with CI's property-testing budget.
[group('Testing')]
[script]
test-late *args:
    import os, subprocess, sys
    environment = dict(os.environ, HYPOTHESIS_PROFILE="ci", FLEXI_LATE_CALLBACKS="0.05")
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "pytest", *sys.argv[1:]], env=environment))

# Test lowest direct dependencies on Python 3.12 in a temporary checkout copy.
[group('Testing')]
[script]
test-floors *args:
    import subprocess, sys
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "--module", "scripts.check_floors", *sys.argv[1:]]))

# Regenerate tracked SVG screenshots and text snapshots for review.
[group('Testing')]
[script]
shots:
    import subprocess
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "python", "scripts/shoot.py"]))

# Build a fresh production wheel and sdist, replacing generated dist/ contents.
[group('Packaging')]
[script]
build:
    import subprocess
    from pathlib import Path
    output = Path("dist")
    if output.is_symlink() or output.is_junction():
        raise SystemExit("Refusing to replace a linked dist directory.")
    raise SystemExit(subprocess.call(["uv", "build", "--clear", "--no-create-gitignore", "--out-dir", str(output)]))

# Build both names from the same source, replacing dist/ and test-dist/.
[group('Packaging')]
[script]
build-staging: build
    import shutil, subprocess
    from pathlib import Path
    output = Path("test-dist")
    if output.is_symlink() or output.is_junction():
        raise SystemExit("Refusing to replace a linked test-dist directory.")
    if output.exists():
        shutil.rmtree(output)
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "--module", "scripts.build_staging", "--dist", "dist", "--out", str(output)]))

# Build, install and test both distributions in temporary directories; no upload.
[group('Packaging')]
[script]
package-check:
    import subprocess
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "--module", "scripts.check_package"]))

# Prepare VERSION metadata and screenshots locally, then check them (writes files).
[group('Release')]
[script]
release-prepare version:
    import subprocess, sys
    title = f"chore(release): {sys.argv[1]}"
    commands = [
        ["uv", "run", "--locked", "--module", "scripts.prepare_release", "prepare", "--root", ".", "--title", title],
        ["uv", "run", "--locked", "python", "scripts/shoot.py"],
        ["uv", "run", "--locked", "--module", "scripts.prepare_release", "check", "--root", ".", "--title", title, "--check-snapshots"],
    ]
    for command in commands:
        result = subprocess.call(command)
        if result:
            raise SystemExit(result)

# Check VERSION metadata and text snapshot headers without changing files.
[group('Release')]
[script]
release-check version:
    import subprocess, sys
    title = f"chore(release): {sys.argv[1]}"
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "--module", "scripts.prepare_release", "check", "--root", ".", "--title", title, "--check-snapshots"]))

# Stage remote main on TestPyPI and test both installed builds; accepts --run/--demo.
[group('Release')]
[script]
try-release *args:
    import subprocess, sys
    raise SystemExit(subprocess.call(["uv", "run", "--locked", "--module", "scripts.try_release", *sys.argv[1:]]))

# Start GitHub CI for a pushed branch (requires authenticated gh).
[group('GitHub')]
[script]
ci branch:
    import subprocess, sys
    raise SystemExit(subprocess.call(["gh", "workflow", "run", "ci.yaml", "--ref", sys.argv[1]]))

# Retry GitHub preparation for a release PR; may commit generated changes to dev.
[group('GitHub')]
[script]
release-retry pr:
    import subprocess, sys
    raise SystemExit(subprocess.call(["gh", "workflow", "run", "release-prepare.yaml", "--ref", "dev", "-f", f"pull_request={sys.argv[1]}"]))
