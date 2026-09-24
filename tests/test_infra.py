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


class TestChromaVectorStoreCompatibility:
    """冒烟测试：验证 ChromaVectorStore 集合列举在不同版本（字符串/对象）下的兼容性"""

    def test_collection_resolution_with_string_collection_names(self, monkeypatch, tmp_path):
        """当 list_collections 返回字符串列表时（如 Chroma 0.6.0+ CollectionName），能正确识别 remembrance_vectors"""
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        from lantai.storage.vector_store import ChromaVectorStore

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.created_name = None

            def list_collections(self):
                # 返回字符串类型的集合名
                return ["remembrance_vectors", "other_vectors"]

            def get_or_create_collection(self, name, **kwargs):
                self.created_name = name
                return {"name": name}

        fake_client = FakeClient()
        monkeypatch.setattr(chromadb, "PersistentClient", lambda **kw: fake_client)
        store = ChromaVectorStore()
        assert fake_client.created_name == "remembrance_vectors"

    def test_collection_resolution_with_object_collections(self, monkeypatch, tmp_path):
        """当 list_collections 返回 Collection 实体对象时（如 Chroma < 0.6.0），能通过 .name 正确识别"""
        import chromadb
        from lantai.storage.vector_store import ChromaVectorStore

        class MockCollectionObj:
            def __init__(self, name):
                self.name = name

            def __repr__(self):
                return f"<Collection object: {self.name}>"

            def __str__(self):
                return repr(self)

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.created_name = None

            def list_collections(self):
                # 返回含有 name 属性但 str(c) 为对象表示的对象
                return [MockCollectionObj("remembrance_vectors")]

            def get_or_create_collection(self, name, **kwargs):
                self.created_name = name
                return {"name": name}

        fake_client = FakeClient()
        monkeypatch.setattr(chromadb, "PersistentClient", lambda **kw: fake_client)
        store = ChromaVectorStore()
        assert fake_client.created_name == "remembrance_vectors"

    def test_collection_resolution_fallback_to_lantai_vectors(self, monkeypatch, tmp_path):
        """当既有集合中不存在 remembrance_vectors 时，回退使用 lantai_vectors"""
        import chromadb
        from lantai.storage.vector_store import ChromaVectorStore

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.created_name = None

            def list_collections(self):
                return []

            def get_or_create_collection(self, name, **kwargs):
                self.created_name = name
                return {"name": name}

        fake_client = FakeClient()
        monkeypatch.setattr(chromadb, "PersistentClient", lambda **kw: fake_client)
        store = ChromaVectorStore()
        assert fake_client.created_name == "lantai_vectors"

