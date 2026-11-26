"""Build dist/philately-lambda.zip for AWS Lambda (Python 3.12, x86_64), without Docker.

Dependencies are installed as Linux wheels even on Windows/macOS (uv's --python-platform), then
zipped together with the app. boto3/botocore are left out because the Lambda runtime provides
them.

    uv run python scripts/build_lambda.py
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "lambda"
OUT = ROOT / "dist" / "philately-lambda.zip"
SKIP_PACKAGES = ("boto3", "botocore", "s3transfer", "jmespath")  # provided by the runtime


def run(*cmd):
    print("+", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)


def main():
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    OUT.parent.mkdir(exist_ok=True)

    requirements = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project"],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout
    kept = [
        line
        for line in requirements.splitlines()
        if line.strip()
        and not line.startswith("#")
        and not line.lower().startswith(tuple(SKIP_PACKAGES))
    ]
    req_file = ROOT / "build" / "requirements-lambda.txt"
    req_file.write_text("\n".join(kept) + "\n")

    run(
        "uv",
        "pip",
        "install",
        "--target",
        str(BUILD),
        "--python-platform",
        "x86_64-manylinux_2_28",
        "--python-version",
        "3.12",
        "--only-binary",
        ":all:",
        "-r",
        str(req_file),
    )

    for name in ("app", "migrations"):
        shutil.copytree(
            ROOT / name, BUILD / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    shutil.copy(ROOT / "lambda_handler.py", BUILD / "lambda_handler.py")
    shutil.copy(ROOT / "wsgi.py", BUILD / "wsgi.py")
    for junk in list(BUILD.rglob("__pycache__")) + list(BUILD.rglob("*.dist-info/RECORD")):
        shutil.rmtree(junk, ignore_errors=True) if junk.is_dir() else junk.unlink(missing_ok=True)

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(BUILD.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(BUILD).as_posix())

    unzipped = sum(p.stat().st_size for p in BUILD.rglob("*") if p.is_file())
    print(
        f"zip: {OUT.stat().st_size / 1e6:.1f} MB, unzipped: {unzipped / 1e6:.1f} MB (limit 250 MB)"
    )
    if unzipped > 250 * 1024 * 1024:
        sys.exit("Package is too large for Lambda")


if __name__ == "__main__":
    main()
