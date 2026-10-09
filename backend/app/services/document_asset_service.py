from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import re
from pathlib import Path
from uuid import uuid4

from defusedxml.ElementTree import fromstring
from PIL import Image

from app.models import DocumentArtifact, DocumentAsset
from app.services.block_service import BlockError, uid


ASSET_MIME_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_ASSET_BYTES = 12 * 1024 * 1024
MAX_DOCX_BYTES = 24 * 1024 * 1024


def _safe_name(value: str, fallback: str) -> str:
    name = Path(value or fallback).name.strip() or fallback
    return re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name)[:180]


class DocumentAssetService:
    """Store PRD binaries on disk while keeping searchable metadata in SQLite."""

    def __init__(self, block_service, root: Path):
        self.blocks = block_service
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_key: str) -> Path:
        path = (self.root / storage_key).resolve()
        if self.root not in path.parents:
            raise BlockError("资产路径无效", 500)
        return path

    @staticmethod
    def public_record(asset: DocumentAsset) -> dict[str, object]:
        return {
            "id": asset.id,
            "document_id": asset.document_id,
            "block_id": asset.block_id,
            "type": asset.type,
            "file_name": asset.file_name,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "width": asset.width,
            "height": asset.height,
            "metadata": asset.metadata_json or {},
            "created_at": asset.created_at.isoformat() if asset.created_at else None,
        }

    def list_records(self, db, document_id: str) -> list[dict[str, object]]:
        return [
            self.public_record(asset)
            for asset in db.query(DocumentAsset)
            .filter_by(document_id=document_id)
            .order_by(DocumentAsset.created_at, DocumentAsset.id)
        ]

    def upload(
        self,
        document_id: str,
        block_id: str | None,
        actor: str,
        file_name: str,
        mime_type: str | None,
        content: bytes,
    ) -> dict[str, object]:
        created: list[Path] = []
        try:
            with self.blocks.write() as db:
                doc = self.blocks.document(db, document_id, actor)
                if block_id is not None:
                    self.blocks.block(db, doc, block_id)
                asset = self.store_asset(
                    db,
                    document_id=document_id,
                    block_id=block_id,
                    actor=actor,
                    file_name=file_name,
                    mime_type=mime_type,
                    content=content,
                    created_paths=created,
                )
                doc.revision += 1
                result = self.public_record(asset)
            return result
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise

    def store_asset(
        self,
        db,
        *,
        document_id: str,
        block_id: str | None,
        actor: str,
        file_name: str,
        mime_type: str | None,
        content: bytes,
        metadata: dict[str, object] | None = None,
        created_paths: list[Path] | None = None,
    ) -> DocumentAsset:
        if not content or len(content) > MAX_ASSET_BYTES:
            raise BlockError("图片必须小于 12 MB", 422)
        guessed = mimetypes.guess_type(file_name)[0]
        normalized_mime = (mime_type or guessed or "").split(";", 1)[0].strip().lower()
        if normalized_mime == "image/jpg":
            normalized_mime = "image/jpeg"
        if normalized_mime not in ASSET_MIME_TYPES:
            raise BlockError("仅支持 PNG、JPEG、WebP 或 SVG 图片", 422)
        width, height = self._image_dimensions(normalized_mime, content)
        asset_id = uid("asset")
        extension = ASSET_MIME_TYPES[normalized_mime]
        storage_key = f"{document_id}/assets/{asset_id}{extension}"
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        if created_paths is not None:
            created_paths.append(path)
        asset = DocumentAsset(
            id=asset_id,
            document_id=document_id,
            block_id=block_id,
            type="image",
            storage_key=storage_key,
            file_name=_safe_name(file_name, f"image{extension}"),
            mime_type=normalized_mime,
            size_bytes=len(content),
            width=width,
            height=height,
            metadata_json=metadata or {},
            created_by=actor,
        )
        db.add(asset)
        db.flush()
        return asset

    def read(self, document_id: str, asset_id: str, actor: str) -> tuple[DocumentAsset, bytes]:
        with self.blocks.factory() as db:
            doc = self.blocks.document(db, document_id, actor)
            asset = db.get(DocumentAsset, asset_id)
            if asset is None or asset.document_id != doc.id:
                raise BlockError("图片不存在", 404)
            return asset, self._path(asset.storage_key).read_bytes()

    def bytes_for(self, asset: DocumentAsset) -> bytes:
        return self._path(asset.storage_key).read_bytes()

    def discard_blocks(self, db, document_id: str, block_ids: set[str]) -> int:
        """Delete the assets anchored to Blocks that are about to disappear.

        Without this, replacing a draft leaves image rows pointing at Block ids
        that no longer exist: unreachable from the document, but still on disk.
        """

        rows = [
            asset for asset in db.query(DocumentAsset).filter_by(document_id=document_id)
            if asset.block_id in block_ids
        ]
        for asset in rows:
            self._path(asset.storage_key).unlink(missing_ok=True)
            db.delete(asset)
        return len(rows)

    def artifact_for_revision(self, db, document_id: str, revision: int, kind: str = "docx"):
        return db.query(DocumentArtifact).filter_by(
            document_id=document_id, revision=revision, kind=kind
        ).one_or_none()

    def store_artifact(
        self,
        db,
        *,
        document_id: str,
        revision: int,
        actor: str,
        file_name: str,
        content: bytes,
        kind: str = "docx",
        mime_type: str = DOCX_MIME_TYPE,
        extension: str = "docx",
        created_paths: list[Path] | None = None,
    ) -> DocumentArtifact:
        if not content or len(content) > MAX_DOCX_BYTES:
            raise BlockError("DOCX 文件必须小于 24 MB", 422)
        digest = hashlib.sha256(content).hexdigest()
        existing = self.artifact_for_revision(db, document_id, revision, kind)
        if existing is not None:
            return existing
        artifact_id = uid("artifact")
        storage_key = f"{document_id}/documents/{revision}-{artifact_id}.{extension}"
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        if created_paths is not None:
            created_paths.append(path)
        artifact = DocumentArtifact(
            id=artifact_id,
            document_id=document_id,
            revision=revision,
            kind=kind,
            storage_key=storage_key,
            file_name=_safe_name(file_name, f"PRD.{extension}"),
            mime_type=mime_type,
            size_bytes=len(content),
            content_hash=digest,
            created_by=actor,
        )
        db.add(artifact)
        db.flush()
        return artifact

    def artifact_bytes(self, artifact: DocumentArtifact) -> bytes:
        return self._path(artifact.storage_key).read_bytes()

    @staticmethod
    def _image_dimensions(mime_type: str, content: bytes) -> tuple[int | None, int | None]:
        if mime_type == "image/svg+xml":
            try:
                root = fromstring(content)
            except Exception as error:
                raise BlockError("SVG 文件无效", 422) from error
            if root.tag.rsplit("}", 1)[-1].lower() != "svg":
                raise BlockError("SVG 文件无效", 422)
            for element in root.iter():
                tag = element.tag.rsplit("}", 1)[-1].lower()
                if tag in {"script", "foreignobject", "iframe", "object", "embed"}:
                    raise BlockError("SVG 包含不安全内容", 422)
                for key, value in element.attrib.items():
                    name = key.rsplit("}", 1)[-1].lower()
                    if name.startswith("on") or (
                        name in {"href", "src"}
                        and not value.startswith(("#", "data:image/"))
                    ):
                        raise BlockError("SVG 包含外部资源或脚本", 422)
            view_box = root.get("viewBox") or root.get("viewbox")
            if view_box:
                try:
                    _, _, width, height = [float(item) for item in view_box.replace(",", " ").split()]
                    return max(1, round(width)), max(1, round(height))
                except (TypeError, ValueError):
                    pass
            return None, None
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
                return image.width, image.height
        except Exception as error:
            raise BlockError("图片文件无效", 422) from error
