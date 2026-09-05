"""向量存储抽象层（当前提供内嵌 ChromaDB 实现，预留存储后端扩展点）"""
from abc import ABC, abstractmethod

from lantai.core.settings import settings


class VectorStore(ABC):
    @abstractmethod
    def add(self, ids: list[str], embeddings: list[list[float]], metadatas: list[dict]):
        ...

    @abstractmethod
    def search(self, query_embedding: list[float], top_k: int,
               filters: dict | None = None) -> list[dict]:
        """返回 [{"id": str, "distance": float, "metadata": dict}]"""
        ...

    @abstractmethod
    def delete(self, ids: list[str]):
        ...


class ChromaVectorStore(VectorStore):
    """ChromaDB 内嵌向量存储，无需外部服务"""

    def __init__(self):
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        self._client = chromadb.PersistentClient(
            path=settings.CHROMADB_PATH,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        # DD-08 命名规范：既有数据库平滑兼容旧名 remembrance_vectors，全新初始化使用 lantai_vectors
        existing = [c.name for c in self._client.list_collections()]
        coll_name = "remembrance_vectors" if "remembrance_vectors" in existing else "lantai_vectors"
        self._collection = self._client.get_or_create_collection(
            name=coll_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, ids: list[str], embeddings: list[list[float]], metadatas: list[dict]):
        if not ids:
            return
        self._collection.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas)

    def search(self, query_embedding: list[float], top_k: int,
               filters: dict | None = None) -> list[dict]:
        kwargs = {"query_embeddings": [query_embedding], "n_results": top_k}
        if filters:
            kwargs["where"] = filters
        result = self._collection.query(**kwargs)
        if not result["ids"]:
            return []
        return [
            {"id": result["ids"][0][i], "distance": result["distances"][0][i],
             "metadata": result["metadatas"][0][i] if result["metadatas"] else {}}
            for i in range(len(result["ids"][0]))
        ]

    def delete(self, ids: list[str]):
        if ids:
            self._collection.delete(ids=ids)


# 全局单例
_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        if settings.VECTOR_STORE_TYPE == "chromadb":
            _store = ChromaVectorStore()
        else:
            raise ValueError(f"Unknown vector store: {settings.VECTOR_STORE_TYPE}")
    return _store
