import logging
import threading

import numpy as np

from app.config import settings

log = logging.getLogger(__name__)

_embedder = None
_lock = threading.Lock()


class ClipEmbedder:
    def __init__(self, device: str):
        import open_clip
        import torch

        if device == "mps" and not torch.backends.mps.is_available():
            device = "cpu"
        self.device = device
        self.torch = torch
        model, _, preprocess = open_clip.create_model_and_transforms(
            settings.clip_model, pretrained=settings.clip_pretrained
        )
        self.model = model.to(device).eval()
        self.preprocess = preprocess
        self.tokenizer = open_clip.get_tokenizer(settings.clip_model)

    def embed_images(self, images_bgr: list) -> np.ndarray:
        from PIL import Image

        vecs = []
        with self.torch.no_grad():
            for start in range(0, len(images_bgr), 64):
                chunk = images_bgr[start : start + 64]
                tensors = self.torch.stack(
                    [self.preprocess(Image.fromarray(img[:, :, ::-1])) for img in chunk]
                ).to(self.device)
                feats = self.model.encode_image(tensors)
                feats = feats / feats.norm(dim=-1, keepdim=True)
                vecs.append(feats.float().cpu().numpy())
        return np.concatenate(vecs) if vecs else np.zeros((0, 512), dtype=np.float32)

    def embed_text(self, text_en: str) -> np.ndarray:
        with self.torch.no_grad():
            tokens = self.tokenizer([text_en]).to(self.device)
            feats = self.model.encode_text(tokens)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.float().cpu().numpy()[0]


def get_embedder() -> ClipEmbedder:
    global _embedder
    with _lock:
        if _embedder is None:
            log.info("Loading CLIP %s/%s …", settings.clip_model, settings.clip_pretrained)
            _embedder = ClipEmbedder(device=settings.yolo_device)
        return _embedder
