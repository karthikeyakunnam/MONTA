"""MONTA — Custom Exceptions."""


class MontaError(Exception):
    """Base exception for MONTA."""
    pass


class UploadError(MontaError):
    """Upload validation or processing error."""
    pass


class UnsupportedFormatError(UploadError):
    """Unsupported video format."""
    pass


class ClipTooLongError(UploadError):
    """Clip exceeds maximum duration."""
    pass


class TooManyClipsError(UploadError):
    """Too many clips for the given resolution."""
    pass


class PipelineError(MontaError):
    """Error in the editing pipeline."""
    pass


class RenderError(MontaError):
    """Error during rendering."""
    pass


class ModelError(MontaError):
    """Error loading or running a model."""
    pass
