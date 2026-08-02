"""
T02: 基础设施栈测试

验证项:
- EMBED_MODEL 默认为 bge-m3
- MemoryItem.embedding JSON 列已删除
- jieba 分词替代 content.split()
- ChromaDB 使用 cosine 距离
"""
import pytest
from remembrance.core.settings import Settings
from remembrance.models.tables import MemoryItem


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


class TestJiebaBM25:
    """hybrid_search 使用 jieba 分词"""

    def test_jieba_imported(self):
        """jieba 模块在 hybrid 模块中可用"""
        from remembrance.retrieval import hybrid
        assert hasattr(hybrid, "jieba")

    def test_chinese_tokenization(self):
        """jieba 对中文正确分词"""
        import jieba
        tokens = list(jieba.lcut("我喜欢Python编程"))
        assert "我" in tokens
        assert "喜欢" in tokens
        assert "Python" in tokens


class TestCosineMetric:
    """ChromaDB 使用 cosine 距离"""

    def test_cosine_space_in_collection(self):
        """ChromaVectorStore 创建 collection 时使用 cosine 距离"""
        from remembrance.storage.vector_store import ChromaVectorStore
        import inspect
        source = inspect.getsource(ChromaVectorStore.__init__)
        assert "cosine" in source
