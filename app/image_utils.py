"""Image validation and preparation utilities.

Validates uploaded NID photo bytes (by actual file content, never by file
extension) and normalizes them into JPEG bytes suitable for sending to the
AI model. No image content is ever logged or printed, in line with PII
safety requirements.
"""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

_CORRUPT_OR_UNSUPPORTED_MESSAGE = (
    "Image file is corrupt or not a supported format (JPG/JPEG/PNG)."
)
_SUPPORTED_FORMATS = frozenset({"JPEG", "PNG"})


class ImageValidationError(Exception):
    """Raised when an uploaded image fails validation.

    The exception message is user-facing and safe to return directly in an
    API error response.
    """


def _verify_image(data: bytes) -> None:
    """Verify that the given bytes decode as a valid image.

    Raises:
        ImageValidationError: If the bytes are not a readable image.
    """
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError(_CORRUPT_OR_UNSUPPORTED_MESSAGE) from exc


def _open_and_check_format(data: bytes) -> Image.Image:
    """Re-open the image and ensure its format is supported.

    Pillow's ``Image.verify()`` invalidates the image object it was called
    on, so the image must be re-opened before further use.

    Returns:
        The opened Pillow ``Image`` in a usable (non-verified) state.

    Raises:
        ImageValidationError: If the bytes cannot be reopened or the
            detected format is not JPEG/PNG.
    """
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError(_CORRUPT_OR_UNSUPPORTED_MESSAGE) from exc

    if image.format not in _SUPPORTED_FORMATS:
        raise ImageValidationError(_CORRUPT_OR_UNSUPPORTED_MESSAGE)
    return image


def _check_minimum_dimension(image: Image.Image, min_dimension: int) -> None:
    """Ensure both sides of the image meet the minimum dimension.

    Raises:
        ImageValidationError: If either side is smaller than
            ``min_dimension``.
    """
    width, height = image.size
    if width < min_dimension or height < min_dimension:
        raise ImageValidationError(
            f"Image is too small (minimum {min_dimension}px on each side). "
            "Please upload a higher-resolution photo."
        )


def _downscale_if_needed(image: Image.Image, max_dimension: int) -> Image.Image:
    """Downscale the image proportionally if its longest side exceeds the max.

    Returns:
        The original image if no resize was needed, otherwise a resized copy.
    """
    width, height = image.size
    longest_side = max(width, height)
    if longest_side <= max_dimension:
        return image

    scale = max_dimension / float(longest_side)
    new_size = (round(width * scale), round(height * scale))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def validate_and_prepare(
    data: bytes,
    min_dimension: int = 300,
    max_dimension: int = 1600,
) -> bytes:
    """Validate uploaded image bytes and normalize them for the AI model.

    Validation is performed on the actual decoded image content, never on
    file extensions or client-supplied metadata. The returned bytes are
    always a JPEG-encoded image with EXIF orientation applied, resized so
    its longest side does not exceed ``max_dimension``, and converted to
    RGB color mode.

    Args:
        data: Raw uploaded file bytes.
        min_dimension: Minimum allowed size, in pixels, for each side of
            the image.
        max_dimension: Maximum allowed size, in pixels, for the longest
            side of the image; larger images are downscaled to fit.

    Returns:
        Normalized JPEG-encoded image bytes.

    Raises:
        ImageValidationError: If the bytes are not a valid, supported
            image, or the image does not meet the minimum dimension
            requirement.
    """
    _verify_image(data)
    image = _open_and_check_format(data)
    _check_minimum_dimension(image, min_dimension)

    image = ImageOps.exif_transpose(image)
    image = _downscale_if_needed(image, max_dimension)

    if image.mode != "RGB":
        image = image.convert("RGB")

    output = BytesIO()
    image.save(output, format="JPEG", quality=85)
    return output.getvalue()
