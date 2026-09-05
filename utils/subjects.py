"""Canonical DSE subjects used to separate study data."""

SUPPORTED_SUBJECTS = ("Math", "M2", "ICT", "Physics")


def normalize_subject(raw: str) -> str:
    """Map a model-detected subject label onto one canonical subject."""
    text = (raw or "").strip().lower()
    if "ict" in text or "information and communication" in text:
        return "ICT"
    if "physic" in text:
        return "Physics"
    if "math" in text and ("module 2" in text or "extended" in text or "m2" in text):
        return "M2"
    if "math" in text:
        return "Math"
    cleaned = " ".join((raw or "").split())
    return cleaned[:40] if cleaned else "Unknown"
