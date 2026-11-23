"""Private object storage. Objects are never public; browsers get short-lived signed URLs."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client


class FileStore(Protocol):
    def put(self, key: str, path: Path) -> None: ...
    def get(self, key: str, path: Path) -> None: ...
    def delete(self, key: str) -> None: ...
    def signed_url(self, key: str, seconds: int) -> str: ...


class S3FileStore:
    """S3-compatible store (MinIO in development).

    Signing is offline, so the signing client points at the public endpoint a browser can
    reach, while reads and writes use the internal endpoint.
    """

    def __init__(
        self, *, endpoint: str, public_endpoint: str, bucket: str, access_key: str, secret_key: str
    ) -> None:
        import boto3
        from botocore.config import Config

        def client(url: str) -> "S3Client":
            return boto3.client(
                "s3",
                endpoint_url=url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name="us-east-1",
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )

        self.bucket = bucket
        self._io = client(endpoint)
        self._signer = client(public_endpoint)

    def put(self, key: str, path: Path) -> None:
        self._io.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": "application/pdf"})

    def get(self, key: str, path: Path) -> None:
        self._io.download_file(self.bucket, key, str(path))

    def delete(self, key: str) -> None:
        self._io.delete_object(Bucket=self.bucket, Key=key)

    def signed_url(self, key: str, seconds: int) -> str:
        params: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "ResponseContentType": "application/pdf",
            "ResponseContentDisposition": "inline",
        }
        return self._signer.generate_presigned_url("get_object", Params=params, ExpiresIn=seconds)
