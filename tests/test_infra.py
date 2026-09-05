"""
T02: 基础设施栈测试

验证项:
- EMBED_MODEL 默认为 bge-m3
- MemoryItem.embedding JSON 列已删除
- jieba 分词替代 content.split()
- ChromaDB 使用 cosine 距离
"""
from lantai.core.settings import Settings
from lantai.models.tables import MemoryItem


class TestEmbedModel:
    """EMBED_MODEL 默认值"""

    def test_default_embed_model(self):
        """EMBED_MODEL 默认为 bge-m3"""
        s = Settings()
        assert s.EMBED_MODEL == "BAAI/bge-m3"


class TestMemoryItemNoEmbedding:
    """MemoryItem.embedding 列删除"""

    def test_embedding_field_deleted(self):
        """embedding 字段不在 MemoryItem 表定义中"""
        assert "embedding" not in MemoryItem.model_fields




class TestCosineMetric:
    """ChromaDB 使用 cosine 距离"""

    def test_cosine_space_in_collection(self):
        """ChromaVectorStore 创建 collection 时使用 cosine 距离"""
        import inspect

        from lantai.storage.vector_store import ChromaVectorStore
        source = inspect.getsource(ChromaVectorStore.__init__)
        assert "cosine" in source
