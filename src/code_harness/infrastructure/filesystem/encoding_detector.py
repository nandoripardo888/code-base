from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DecodedText:
    text: str
    encoding: str


def appears_binary(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:8192]
    if b"\x00" in sample:
        return True
    controls = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return controls / len(sample) > 0.10


def decode_source(data: bytes) -> DecodedText | None:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return DecodedText(data.decode(encoding), encoding)
        except UnicodeDecodeError:
            continue
    return None
