"""Audio formats at the edges of a call.

Nova 2 Sonic takes and returns 16-bit little-endian mono PCM at 8, 16 or 24 kHz (Nova 2 Sonic
input events docs), and the Strands Nova Sonic provider sends whatever rate it is configured with
without resampling. So nothing here resamples:

    browser  mic -> AudioContext at 16 kHz -> PCM16 16 kHz -> Sonic -> PCM16 16 kHz -> speaker
    phone    Twilio mu-law 8 kHz -> PCM16 8 kHz -> Sonic -> PCM16 8 kHz -> mu-law 8 kHz -> Twilio

The only conversion is G.711 mu-law, done with lookup tables in pure Python. The stdlib `audioop`
is gone in Python 3.13; the tests compare against it while 3.12 still has it.
"""

from __future__ import annotations

import sys
from array import array

BROWSER_RATE = 16000
PHONE_RATE = 8000
FRAME_MS = 20

_BIAS = 0x84


def _decode_sample(u: int) -> int:
    u = ~u & 0xFF
    magnitude = (((u & 0x0F) << 3) + _BIAS) << ((u >> 4) & 0x07)
    sample = magnitude - _BIAS
    return -sample if u & 0x80 else sample


_SEG_END = (0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF)


def _encode_sample(s: int) -> int:
    """G.711 on the 14-bit value, as CPython's audioop `st_14linear2ulaw` does."""
    v = s >> 2
    if v < 0:
        v, mask = -v, 0x7F
    else:
        mask = 0xFF
    v = min(v, 8159) + (_BIAS >> 2)
    seg = next((i for i, end in enumerate(_SEG_END) if v <= end), 8)
    if seg >= 8:
        return 0x7F ^ mask
    return ((seg << 4) | ((v >> (seg + 1)) & 0x0F)) ^ mask


# 256 little-endian PCM16 pairs, indexed by the mu-law byte.
_DECODE = [array("h", [_decode_sample(u)]) for u in range(256)]
if sys.byteorder != "little":  # pragma: no cover - AgentCore and Macs are little-endian
    for a in _DECODE:
        a.byteswap()
_DECODE_BYTES = [a.tobytes() for a in _DECODE]
# 65536 mu-law bytes, indexed by the sample as an unsigned 16-bit number.
_ENCODE = bytes(_encode_sample(s - 65536 if s >= 32768 else s) for s in range(65536))


def ulaw_to_pcm16(data: bytes) -> bytes:
    return b"".join(_DECODE_BYTES[b] for b in data)


def pcm16_to_ulaw(data: bytes) -> bytes:
    if len(data) % 2:
        data = data[:-1]
    samples = array("H")
    samples.frombytes(data)
    if sys.byteorder != "little":  # pragma: no cover
        samples.byteswap()
    return bytes(_ENCODE[s] for s in samples)


def frame_bytes(rate: int, *, sample_width: int = 2) -> int:
    """Bytes in one 20 ms frame."""
    return rate * FRAME_MS // 1000 * sample_width


def seconds_of_pcm16(data: bytes | int, rate: int) -> float:
    n = data if isinstance(data, int) else len(data)
    return n / (2 * rate)
