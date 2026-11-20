"""極簡 WAV 讀寫（取代 scipy.io.wavfile，讓執行時不需要 scipy）。

讀：PCM 16/24/32-bit、IEEE float 32/64-bit、WAVE_FORMAT_EXTENSIBLE；未知 chunk 會略過。
寫：32-bit float mono/stereo。
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

_PCM, _FLOAT, _EXT = 1, 3, 0xFFFE


def read_wav(path: str | Path) -> tuple[int, np.ndarray]:
    """回傳 (samplerate, float32 陣列)；多聲道為 (n, ch)。"""
    data = Path(path).read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("不是 WAV 檔")
    pos, fmt, body = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos : pos + 4], struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        chunk = data[pos + 8 : pos + 8 + size]
        if cid == b"fmt ":
            fmt = chunk
        elif cid == b"data":
            body = chunk
        pos += 8 + size + (size & 1)
    if fmt is None or body is None or len(fmt) < 16:
        raise ValueError("WAV 格式不完整")
    tag, ch, sr, _, align, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == _EXT and len(fmt) >= 26:
        tag = struct.unpack("<H", fmt[24:26])[0]
    if ch < 1 or align < 1:
        raise ValueError("WAV 格式錯誤")
    body = body[: len(body) - len(body) % align]
    if tag == _FLOAT and bits == 32:
        x = np.frombuffer(body, dtype="<f4").astype(np.float32)
    elif tag == _FLOAT and bits == 64:
        x = np.frombuffer(body, dtype="<f8").astype(np.float32)
    elif tag == _PCM and bits == 16:
        x = np.frombuffer(body, dtype="<i2").astype(np.float32) / 32768.0
    elif tag == _PCM and bits == 32:
        x = np.frombuffer(body, dtype="<i4").astype(np.float32) / 2147483648.0
    elif tag == _PCM and bits == 24:
        b = np.frombuffer(body, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        x = v.astype(np.float32) / 8388608.0
    else:
        raise ValueError(f"不支援的 WAV 編碼（tag={tag}, bits={bits}）")
    if ch > 1:
        x = x.reshape(-1, ch)
    return sr, x


def write_wav(path: str | Path, sr: int, x: np.ndarray, pcm16: bool = False) -> None:
    """預設寫 float32；pcm16=True 寫 16-bit PCM（體積減半，用於保留原始錄音）。"""
    x = np.asarray(x, dtype="<f4")
    ch = 1 if x.ndim == 1 else x.shape[1]
    if pcm16:
        raw = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        fmt = struct.pack("<HHIIHH", _PCM, ch, sr, sr * ch * 2, ch * 2, 16)
    else:
        raw = x.tobytes()
        fmt = struct.pack("<HHIIHH", _FLOAT, ch, sr, sr * ch * 4, ch * 4, 32)
    out = b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(raw)) + b"WAVE"
    out += b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(raw)) + raw
    Path(path).write_bytes(out)
