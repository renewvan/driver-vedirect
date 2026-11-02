"""VE.Direct text-mode framing: bytes in, validated field dicts out.

A text block is `\\r\\nKEY\\tVALUE` lines ending in `\\r\\nChecksum\\t<1 byte>`; all bytes of the
block, checksum included, sum to 0 mod 256. HEX messages (`:` ... `\\n`) are interleaved with
text blocks (even inside one) and are excluded from the sum, so they are skipped here.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CHECKSUM_TAG = b"\nChecksum\t"
_MAX_BLOCK = 4096  # a runaway buffer means we lost sync; start over


class FrameParser:
    def __init__(self) -> None:
        self._buf = bytearray()
        self._in_hex = False
        self._checksum_next = False
        self.bad_frames = 0

    def feed(self, data: bytes) -> list[dict[str, str]]:
        """Consume bytes; return every block completed (and valid) by them."""
        frames: list[dict[str, str]] = []
        for byte in data:
            if self._in_hex:
                self._in_hex = byte != 0x0A
                continue
            if byte == 0x3A and not self._checksum_next:  # ':' starts a HEX message
                self._in_hex = True
                continue
            self._buf.append(byte)
            if self._checksum_next:
                self._checksum_next = False
                block, self._buf = bytes(self._buf), bytearray()
                if sum(block) % 256 == 0:
                    frames.append(_parse_block(block))
                else:
                    self.bad_frames += 1
                    logger.debug(
                        "Dropped VE.Direct block with bad checksum (%d so far)", self.bad_frames
                    )
            elif self._buf.endswith(_CHECKSUM_TAG):
                self._checksum_next = True
            elif len(self._buf) > _MAX_BLOCK:
                self._buf.clear()
        return frames


def _parse_block(block: bytes) -> dict[str, str]:
    fields: dict[str, str] = {}
    body = block[: block.rindex(_CHECKSUM_TAG)]
    for line in body.decode("ascii", errors="replace").split("\r\n"):
        key, sep, value = line.partition("\t")
        if sep:
            fields[key] = value
    return fields
