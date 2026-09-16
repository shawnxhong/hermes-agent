#!/usr/bin/env python3
"""Local Kokoro command adapter; preserves the installed English voice path."""

import argparse
import re
from pathlib import Path

import numpy as np
import soundfile as sf
from typing import Any

Kokoro = Any


_HAN_RE = re.compile(r"[\u3400-\u9fff]")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
_TECH_TOKEN_RE = re.compile(
    r"[A-Za-z0-9]+(?:[._+/#%°-][A-Za-z0-9]+)*"
)


def split_language_runs(text: str) -> list[tuple[str, str]]:
    """Split Chinese/English text without losing punctuation or spacing.

    Han characters and ASCII letters are the strong language signals.
    Digits and punctuation stay with their surrounding run, except technical
    tokens containing Latin letters (for example ``Qwen3.6-35B`` or ``25°C``),
    which are kept intact for the English frontend.
    """
    if not text:
        return []

    labels: list[str | None] = []
    for char in text:
        if _HAN_RE.match(char):
            labels.append("zh")
        elif _ASCII_LETTER_RE.match(char):
            labels.append("en")
        else:
            labels.append(None)

    for match in _TECH_TOKEN_RE.finditer(text):
        token = match.group(0)
        if _ASCII_LETTER_RE.search(token):
            for index in range(match.start(), match.end()):
                if labels[index] != "zh":
                    labels[index] = "en"

    runs: list[tuple[str, str]] = []
    current_language: str | None = None
    current: list[str] = []
    pending: list[str] = []

    for char, language in zip(text, labels):
        if language is None:
            pending.append(char)
            continue
        if current_language is None:
            current.extend(pending)
            pending.clear()
            current_language = language
        elif language != current_language:
            current.extend(pending)
            pending.clear()
            segment = "".join(current)
            if segment.strip():
                runs.append((current_language, segment))
            current = []
            current_language = language
        else:
            current.extend(pending)
            pending.clear()
        current.append(char)

    current.extend(pending)
    segment = "".join(current)
    if segment.strip():
        runs.append((current_language or "en", segment))
    return runs


def _chinese_g2p(kokoro: Kokoro):
    """Create the Chinese frontend with an English fallback for edge cases."""
    from misaki import zh

    return zh.ZHG2P(
        version="1.1",
        en_callable=lambda value: kokoro.tokenizer.phonemize(value, "en-us"),
    )


def synthesize_mixed(
    kokoro: Kokoro,
    text: str,
    *,
    chinese_voice: str,
    english_voice: str,
    speed: float,
) -> tuple[np.ndarray, int]:
    """Synthesize each language run with its own frontend and voice."""
    runs = split_language_runs(text)
    if not runs:
        raise ValueError("TTS input is empty")

    # Preserve the previous fast path and prosody for single-language input.
    if len(runs) == 1:
        language, segment = runs[0]
        if language == "zh":
            phonemes, _ = _chinese_g2p(kokoro)(segment)
            return kokoro.create(
                phonemes,
                voice=chinese_voice,
                speed=speed,
                is_phonemes=True,
            )
        return kokoro.create(
            segment,
            voice=english_voice,
            speed=speed,
            lang="en-us",
            is_phonemes=False,
        )

    chinese_g2p = _chinese_g2p(kokoro)
    chunks: list[np.ndarray] = []
    sample_rate: int | None = None
    for language, segment in runs:
        if language == "zh":
            phonemes, _ = chinese_g2p(segment)
            samples, rate = kokoro.create(
                phonemes,
                voice=chinese_voice,
                speed=speed,
                is_phonemes=True,
            )
        else:
            samples, rate = kokoro.create(
                segment,
                voice=english_voice,
                speed=speed,
                lang="en-us",
                is_phonemes=False,
            )
        if sample_rate is None:
            sample_rate = rate
        elif rate != sample_rate:
            raise ValueError(
                f"Kokoro returned inconsistent sample rates: {sample_rate} and {rate}"
            )
        if chunks:
            chunks.append(np.zeros(int(rate * 0.06), dtype=np.float32))
        chunks.append(np.asarray(samples, dtype=np.float32).reshape(-1))

    return np.concatenate(chunks), int(sample_rate)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--voice", default="zf_001")
    parser.add_argument("--english-voice", default="af_maple")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    model_dir = args.model_dir.expanduser()
    text = Path(args.input).read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit("TTS input is empty")

    from kokoro_onnx import Kokoro

    kokoro = Kokoro(
        str(model_dir / "kokoro-v1.1-zh.onnx"),
        str(model_dir / "voices-v1.1-zh.bin"),
        vocab_config=str(model_dir / "config.json"),
    )
    samples, sample_rate = synthesize_mixed(
        kokoro,
        text,
        chinese_voice=args.voice,
        english_voice=args.english_voice,
        speed=args.speed,
    )
    sf.write(args.output, samples, sample_rate)


if __name__ == "__main__":
    main()
