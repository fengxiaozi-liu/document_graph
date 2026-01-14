from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from document_graph.config import load_app_config
from document_graph.document_parsing import MissingParserDependency


@dataclass(frozen=True)
class MultimodalModel:
    device: str
    dim: int
    model: object
    preprocess: object
    tokenizer: object


@lru_cache(maxsize=1)
def _load_openclip_model() -> MultimodalModel:
    cfg = load_app_config()
    if not cfg.multimodal.enabled:
        raise RuntimeError("multimodal_disabled")
    if cfg.multimodal.backend != "openclip":
        raise RuntimeError(f"unsupported_multimodal_backend: {cfg.multimodal.backend}")
    try:
        import torch
    except ImportError as exc:
        raise MissingParserDependency("missing dependency: torch") from exc
    try:
        import open_clip
    except ImportError as exc:
        raise MissingParserDependency("missing dependency: open_clip_torch") from exc

    device = cfg.multimodal.device or "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms(cfg.multimodal.model, pretrained=cfg.multimodal.pretrained)
    tokenizer = open_clip.get_tokenizer(cfg.multimodal.model)
    model.eval()
    model.to(device)
    dim = int(getattr(model, "text_projection", None).shape[1]) if getattr(model, "text_projection", None) is not None else 512
    return MultimodalModel(device=device, dim=dim, model=model, preprocess=preprocess, tokenizer=tokenizer)


def image_embedding(path: Path) -> list[float]:
    cfg = load_app_config()
    if not cfg.multimodal.enabled:
        raise RuntimeError("multimodal_disabled")
    mm = _load_openclip_model()
    try:
        import torch
    except ImportError as exc:
        raise MissingParserDependency("missing dependency: torch") from exc
    try:
        from PIL import Image
    except ImportError as exc:
        raise MissingParserDependency("missing dependency: pillow") from exc

    img = Image.open(path).convert("RGB")
    x = mm.preprocess(img).unsqueeze(0).to(mm.device)  # type: ignore[attr-defined]
    with torch.no_grad():
        vec = mm.model.encode_image(x)  # type: ignore[attr-defined]
        vec = vec / vec.norm(dim=-1, keepdim=True)
    return [float(v) for v in vec[0].detach().cpu().tolist()]


def text_embedding(text: str) -> list[float]:
    cfg = load_app_config()
    if not cfg.multimodal.enabled:
        raise RuntimeError("multimodal_disabled")
    mm = _load_openclip_model()
    try:
        import torch
    except ImportError as exc:
        raise MissingParserDependency("missing dependency: torch") from exc

    tokens = mm.tokenizer([text])  # type: ignore[call-arg]
    tokens = tokens.to(mm.device)  # type: ignore[attr-defined]
    with torch.no_grad():
        vec = mm.model.encode_text(tokens)  # type: ignore[attr-defined]
        vec = vec / vec.norm(dim=-1, keepdim=True)
    return [float(v) for v in vec[0].detach().cpu().tolist()]


def embedding_dim() -> int:
    mm = _load_openclip_model()
    return int(mm.dim)

