"""Lazy local embeddings; model cache stays outside the repository."""
import os
import struct
import threading
from pathlib import Path


class LocalEmbedding:
    name = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
    version = '1'
    dimension = 384
    _shared_models = {}
    _load_lock = threading.Lock()

    def __init__(self, name=None, version=None, dimension=None):
        self._model = None
        if name: self.name = name
        if version: self.version = version
        if dimension: self.dimension = dimension

    def encode(self, text):
        with self._load_lock:
            key = (self.name, self.version)
            if key not in self.__class__._shared_models:
                from sentence_transformers import SentenceTransformer
                cache = Path.home() / '.cache' / 'calendar-agent-models'
                self.__class__._shared_models[key] = SentenceTransformer(self.name, cache_folder=str(cache),
                                                 revision=None if self.version=='1' else self.version,
                                                 local_files_only=os.getenv('EMBEDDING_ALLOW_DOWNLOAD') != '1')
            self._model = self.__class__._shared_models[key]
            self.dimension = self._model.get_sentence_embedding_dimension()
        vector = self._model.encode(text, normalize_embeddings=True)
        return struct.pack(f'{len(vector)}f', *vector)
