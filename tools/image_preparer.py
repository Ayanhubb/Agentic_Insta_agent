"""Prepare a still image for Instagram feed publishing."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from config import Settings
from models.errors import AppError, ErrorCode
from models.observations import Observation
from models.state import AgentState
from tools.base import Tool

MIN_ASPECT_RATIO = 4 / 5
MAX_ASPECT_RATIO = 1.91


class ImagePreparer(Tool):
    name = "prepare_image"
    purpose = "Prepare image for publishing"
    description = (
        "Normalize a validated image into a deterministic JPEG without modifying "
        "the original upload."
    )

    def __init__(
        self,
        settings: Settings | Path | None = None,
        *,
        output_dir: Path | None = None,
        max_image_bytes: int = 8 * 1024 * 1024,
        min_image_side: int = 320,
        target_image_width: int = 1080,
        jpeg_quality: int = 90,
    ) -> None:
        if isinstance(settings, Settings):
            output = settings.output_dir
            max_image_bytes = int(settings.max_image_bytes or 8 * 1024 * 1024)
            min_image_side = settings.min_image_side
            target_image_width = settings.target_image_width
            jpeg_quality = settings.jpeg_quality
        elif isinstance(settings, Path):
            output = settings
        else:
            output = output_dir or Path("output")
        prepared = Path(output)
        if prepared.name != "prepared":
            prepared = prepared / "prepared"
        self._output_dir = prepared
        self._max_image_bytes = max_image_bytes
        self._min_image_side = min_image_side
        self._target_image_width = target_image_width
        self._jpeg_quality = jpeg_quality

    async def execute(self, state: AgentState) -> Observation:
        if not state.image_path:
            raise AppError(ErrorCode.INVALID_IMAGE, "No image is available to prepare.")
        prepared_path = self.prepare(Path(state.image_path), state.task_id)
        prepared = str(prepared_path)
        state.temp_paths.append(prepared)
        return Observation(
            success=True,
            tool=self.name,
            data={"prepared_image_path": prepared},
        )

    def prepare(self, image_path: Path | str, task_id: str = "prepared") -> Path:
        source = Path(image_path).resolve()
        self._output_dir.mkdir(parents=True, exist_ok=True)
        destination = (self._output_dir / f"{task_id}.jpg").resolve()
        return self._save_prepared(source, destination)

    def prepare_to(self, image_path: Path | str, destination: Path) -> Path:
        source = Path(image_path).resolve()
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        return self._save_prepared(source, dest.resolve())

    def _save_prepared(self, source: Path, destination: Path) -> Path:
        if destination == source:
            raise AppError(
                ErrorCode.INVALID_FILE,
                "Preparation refused to overwrite the original image.",
                http_status=500,
            )
        if destination.exists() and self._is_usable_prepared(destination):
            return destination
        payload = self._render(source)
        tmp_path = destination.with_name(destination.name + ".tmp")
        try:
            tmp_path.write_bytes(payload)
            tmp_path.replace(destination)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            raise AppError(
                ErrorCode.PREPARATION_FAILED,
                "The prepared image could not be written.",
                http_status=500,
            ) from exc
        return destination

    def _render(self, source: Path) -> bytes:
        try:
            with Image.open(source) as image:
                transposed = ImageOps.exif_transpose(image)
                rgb = self._to_rgb(transposed)
                fitted = self._fit_aspect_ratio(rgb)
                resized = self._resize(fitted)
                return self._encode_jpeg(resized)
        except AppError:
            raise
        except UnidentifiedImageError as exc:
            raise AppError(
                ErrorCode.CORRUPTED_IMAGE,
                "The image could not be prepared because it is unreadable.",
                http_status=400,
            ) from exc
        except OSError as exc:
            raise AppError(
                ErrorCode.CORRUPTED_IMAGE,
                "The image could not be prepared.",
                http_status=400,
            ) from exc

    def _is_usable_prepared(self, path: Path) -> bool:
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
                return (image.format or "").upper() == "JPEG"
        except (OSError, UnidentifiedImageError, ValueError):
            return False

    def _to_rgb(self, image: Image.Image) -> Image.Image:
        if image.mode in {"RGBA", "LA", "P"}:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            return background
        return image.convert("RGB")

    def _fit_aspect_ratio(self, image: Image.Image) -> Image.Image:
        width, height = image.size
        if height <= 0 or width <= 0:
            raise AppError(
                ErrorCode.INVALID_DIMENSIONS,
                "Image width and height must be positive.",
                http_status=400,
            )
        aspect = width / height
        if MIN_ASPECT_RATIO <= aspect <= MAX_ASPECT_RATIO:
            return image
        if aspect > MAX_ASPECT_RATIO:
            new_width = max(int(height * MAX_ASPECT_RATIO), self._min_image_side)
            left = max((width - new_width) // 2, 0)
            return image.crop((left, 0, left + new_width, height))
        new_height = max(int(width / MIN_ASPECT_RATIO), self._min_image_side)
        top = max((height - new_height) // 2, 0)
        return image.crop((0, top, width, top + new_height))

    def _resize(self, image: Image.Image) -> Image.Image:
        width, height = image.size
        if width <= self._target_image_width and height >= self._min_image_side:
            return image
        if width > self._target_image_width:
            ratio = self._target_image_width / width
            new_size = (
                self._target_image_width,
                max(int(height * ratio), self._min_image_side),
            )
            return image.resize(new_size, Image.Resampling.LANCZOS)
        return image

    def _encode_jpeg(self, image: Image.Image) -> bytes:
        quality = self._jpeg_quality
        while quality >= 50:
            buffer = BytesIO()
            image.save(
                buffer,
                format="JPEG",
                quality=quality,
                optimize=False,
                progressive=False,
                subsampling=0,
                exif=b"",
            )
            payload = buffer.getvalue()
            if len(payload) <= self._max_image_bytes:
                return payload
            quality -= 10
        raise AppError(
            ErrorCode.FILE_TOO_LARGE,
            "The prepared image exceeds the maximum allowed file size.",
            http_status=400,
        )
