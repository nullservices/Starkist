"""Gain-invariant onset fingerprints and normalized cross-correlation against them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from starkist.features import DEFAULT_MEL, SILENCE_LEVEL_DB, MelParams, log_mel
from starkist.wav import read_wav, resample

NO_SCORE = -1.0
_BLOCK = 2048  # windows scored per batch; bounds the normalised-window buffer
ONSET_S = 0.8  # only the start of each reference is fingerprinted


@dataclass(frozen=True)
class Fingerprint:
    name: str
    feats: np.ndarray  # (L, n_mels), normalized by normalize_block

    @property
    def length(self) -> int:
        return len(self.feats)


def normalize_block(feats: np.ndarray) -> np.ndarray:
    """Remove each frame's mean across bands (gain, flat background), then zero-mean unit-norm."""
    f = feats - feats.mean(axis=-1, keepdims=True)
    f = f - f.mean(axis=(-2, -1), keepdims=True)
    norm = np.sqrt((f * f).sum(axis=(-2, -1), keepdims=True)) + 1e-9
    return f / norm


def build_fingerprint(
    name: str, samples: np.ndarray, p: MelParams, onset_s: float = ONSET_S
) -> Fingerprint:
    feats = log_mel(samples[: int(onset_s * p.sample_rate)], p)
    if len(feats) < 2:
        raise ValueError(f"reference {name!r} is too short for a {onset_s}s onset fingerprint")
    return Fingerprint(name, normalize_block(feats).astype(np.float32))


def score_sequence(feats: np.ndarray, fp: Fingerprint) -> np.ndarray:
    """NCC of every trailing L-frame window of `feats` with `fp`.

    NO_SCORE before L frames exist and for windows quieter than SILENCE_LEVEL_DB.
    """
    t, length = len(feats), fp.length
    out = np.full(t, NO_SCORE, dtype=np.float32)
    if t < length:
        return out
    windows = sliding_window_view(feats, (length, feats.shape[1]))[:, 0]  # (t-L+1, L, n_mels)
    level = np.convolve(feats.mean(axis=1), np.ones(length) / length, mode="valid")
    for start in range(0, len(windows), _BLOCK):
        block = windows[start : start + _BLOCK]
        scores = np.einsum("tij,ij->t", normalize_block(block), fp.feats)
        quiet = level[start : start + len(block)] < SILENCE_LEVEL_DB
        lo = length - 1 + start
        out[lo : lo + len(block)] = np.where(quiet, NO_SCORE, scores)
    return out


def load_fingerprints(
    folder: Path, p: MelParams = DEFAULT_MEL, onset_s: float = ONSET_S
) -> tuple[Fingerprint, ...]:
    """One fingerprint per .wav in `folder`, named by file stem, in sorted order."""
    paths = sorted(Path(folder).glob("*.wav"))
    if not paths:
        raise FileNotFoundError(f"no .wav reference files in {folder}")
    out = []
    for path in paths:
        y, rate = read_wav(path)
        out.append(build_fingerprint(path.stem, resample(y, rate, p.sample_rate), p, onset_s))
    return tuple(out)
