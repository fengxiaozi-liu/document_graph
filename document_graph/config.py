from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml


def _env(key: str) -> Optional[str]:
    value = os.environ.get(key)
    return value if value else None


def _deep_update(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v
    return dst


@dataclass(frozen=True)
class OpenAICompatConfig:
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class LLMConfig(OpenAICompatConfig):
    temperature: float = 0.2


@dataclass(frozen=True)
class EmbeddingConfig(OpenAICompatConfig):
    batch_size: int = 64


@dataclass(frozen=True)
class QdrantConfig:
    url: str
    distance: str = "Cosine"


@dataclass(frozen=True)
class ChunkingConfig:
    target_chars: int = 1200
    max_chars: int = 2400
    overlap_chars: int = 200


@dataclass(frozen=True)
class OCRConfig:
    enabled: bool = True
    lang: str = "eng"
    tesseract_cmd: str | None = None


@dataclass(frozen=True)
class MultimodalConfig:
    enabled: bool = False
    backend: str = "openclip"
    model: str = "ViT-B-32"
    pretrained: str = "laion2b_s34b_b79k"
    device: str = "cpu"


@dataclass(frozen=True)
class AppConfig:
    llm: LLMConfig
    embedding: EmbeddingConfig
    qdrant: QdrantConfig
    chunking: ChunkingConfig = ChunkingConfig()
    ocr: OCRConfig = OCRConfig()
    multimodal: MultimodalConfig = MultimodalConfig()


def load_app_config(path: str = "config.yaml") -> AppConfig:
    config_path = Path(path)
    data: dict[str, Any] = {}
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    overrides: dict[str, Any] = {}
    if _env("LLM_BASE_URL"):
        overrides.setdefault("llm", {})["base_url"] = _env("LLM_BASE_URL")
    if _env("LLM_API_KEY"):
        overrides.setdefault("llm", {})["api_key"] = _env("LLM_API_KEY")
    if _env("LLM_MODEL"):
        overrides.setdefault("llm", {})["model"] = _env("LLM_MODEL")
    if _env("EMBEDDING_BASE_URL"):
        overrides.setdefault("embedding", {})["base_url"] = _env("EMBEDDING_BASE_URL")
    if _env("EMBEDDING_API_KEY"):
        overrides.setdefault("embedding", {})["api_key"] = _env("EMBEDDING_API_KEY")
    if _env("EMBEDDING_MODEL"):
        overrides.setdefault("embedding", {})["model"] = _env("EMBEDDING_MODEL")
    if _env("QDRANT_URL"):
        overrides.setdefault("qdrant", {})["url"] = _env("QDRANT_URL")

    if overrides:
        _deep_update(data, overrides)

    llm = data.get("llm") or {}
    embedding = data.get("embedding") or {}
    qdrant = data.get("qdrant") or {}
    chunking = data.get("chunking") or {}
    ocr = data.get("ocr") or {}
    multimodal = data.get("multimodal") or {}

    return AppConfig(
        llm=LLMConfig(
            base_url=str(llm["base_url"]).rstrip("/"),
            api_key=str(llm["api_key"]),
            model=str(llm["model"]),
            temperature=float(llm.get("temperature", 0.2)),
        ),
        embedding=EmbeddingConfig(
            base_url=str(embedding["base_url"]).rstrip("/"),
            api_key=str(embedding["api_key"]),
            model=str(embedding["model"]),
            batch_size=int(embedding.get("batch_size", 64)),
        ),
        qdrant=QdrantConfig(
            url=str(qdrant.get("url", "http://localhost:6333")).rstrip("/"),
            distance=str(qdrant.get("distance", "Cosine")),
        ),
        chunking=ChunkingConfig(
            target_chars=int(chunking.get("target_chars", 1200)),
            max_chars=int(chunking.get("max_chars", 2400)),
            overlap_chars=int(chunking.get("overlap_chars", 200)),
        ),
        ocr=OCRConfig(
            enabled=bool(ocr.get("enabled", True)),
            lang=str(ocr.get("lang", "eng")),
            tesseract_cmd=str(ocr.get("tesseract_cmd")) if ocr.get("tesseract_cmd") else _env("TESSERACT_CMD"),
        ),
        multimodal=MultimodalConfig(
            enabled=bool(multimodal.get("enabled", False)),
            backend=str(multimodal.get("backend", "openclip")),
            model=str(multimodal.get("model", "ViT-B-32")),
            pretrained=str(multimodal.get("pretrained", "laion2b_s34b_b79k")),
            device=str(multimodal.get("device", "cpu")),
        ),
    )

