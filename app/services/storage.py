"""Where uploaded images are stored.

`local` keeps files in UPLOAD_FOLDER, which is fine for development but is wiped on every deploy
on hosts with ephemeral disks (Render's free tier, Heroku). `s3` stores them in any
S3-compatible bucket (AWS S3, Cloudflare R2, Backblaze B2, MinIO), so they survive deploys and
can be served from a CDN.

Templates keep linking to /uploads/<name>. With S3 that route redirects to the bucket (a public
URL, or a short-lived signed URL for private buckets).
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, current_app


class LocalStorage:
    is_remote = False

    def __init__(self, folder: str):
        self.folder = Path(folder)

    def save(self, filename: str, data: bytes, content_type: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / filename).write_bytes(data)

    def read(self, filename: str) -> bytes:
        return (self.folder / filename).read_bytes()

    def delete(self, filename: str) -> None:
        (self.folder / filename).unlink(missing_ok=True)

    def url(self, filename: str) -> str | None:
        return None  # served by the /uploads route


class S3Storage:
    is_remote = True

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        public_url: str | None = None,
        client=None,
        **client_kwargs,
    ):
        if client is None:
            import boto3
            from botocore.config import Config

            # SigV4 is required by newer regions (e.g. Mumbai) for presigned URLs; boto3's
            # default can fall back to the old scheme, which those regions reject.
            client = boto3.client(
                "s3",
                config=Config(signature_version="s3v4"),
                **{k: v for k, v in client_kwargs.items() if v},
            )
        self.client = client
        self.bucket = bucket
        self.prefix = prefix
        self.public_url = public_url.rstrip("/") if public_url else None

    def _key(self, filename: str) -> str:
        return f"{self.prefix}{filename}"

    def save(self, filename: str, data: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._key(filename),
            Body=data,
            ContentType=content_type,
            # Names are random and never reused, so files can be cached for a long time.
            CacheControl="public, max-age=31536000, immutable",
        )

    def read(self, filename: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._key(filename))["Body"].read()

    def delete(self, filename: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(filename))

    def url(self, filename: str) -> str:
        if self.public_url:
            return f"{self.public_url}/{self._key(filename)}"
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": self._key(filename)}, ExpiresIn=3600
        )


def init_storage(app: Flask) -> None:
    config = app.config
    if config["STORAGE_BACKEND"] == "s3":
        if not config.get("S3_BUCKET"):
            raise RuntimeError("STORAGE_BACKEND=s3 needs S3_BUCKET (see .env.example).")
        storage = S3Storage(
            config["S3_BUCKET"],
            prefix=config.get("S3_PREFIX") or "",
            public_url=config.get("S3_PUBLIC_URL"),
            region_name=config.get("S3_REGION"),
            # Use the bucket's regional endpoint for AWS S3; the global one answers presigned
            # requests for non-us-east-1 buckets with a redirect, which breaks signed URLs.
            endpoint_url=config.get("S3_ENDPOINT_URL")
            or (
                f"https://s3.{config['S3_REGION']}.amazonaws.com"
                if config.get("S3_REGION")
                else None
            ),
            aws_access_key_id=config.get("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=config.get("S3_SECRET_ACCESS_KEY"),
        )
    elif config["STORAGE_BACKEND"] == "local":
        storage = LocalStorage(config["UPLOAD_FOLDER"])
    else:
        raise RuntimeError(f"Unknown STORAGE_BACKEND {config['STORAGE_BACKEND']!r}")
    app.extensions["storage"] = storage


def get_storage() -> LocalStorage | S3Storage:
    return current_app.extensions["storage"]
