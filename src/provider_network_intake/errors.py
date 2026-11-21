"""Public failures distinguish malformed input, unsupported work and stale decisions."""


class IntakeError(ValueError):
    """Invalid input or unsupported operation; no result was accepted."""


class Conflict(IntakeError):
    """An expected input/catalog revision no longer matches."""


class ResourceLimit(IntakeError):
    """A declared resource bound was exceeded."""
