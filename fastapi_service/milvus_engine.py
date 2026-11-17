import logging
import time
from pymilvus import MilvusClient
from openai import OpenAI
from config import MILVUS_URI, EMBEDDING_API_KEY, EMBEDDING_BASE_URL

logger = logging.getLogger(__name__)

# Milvus 客户端懒加载 + 重试
_milvus_client = None

def get_milvus_client():
    global _milvus_client
    if _milvus_client is not None:
        return _milvus_client
    
    max_retries = 10
    for attempt in range(max_retries):
        try:
            _milvus_client = MilvusClient(uri=MILVUS_URI)
            logger.info(f"Milvus 客户端连接成功 (尝试 {attempt+1}/{max_retries})")
            return _milvus_client
        except Exception as e:
            logger.warning(f"Milvus 连接失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(5)  # 等 5 秒后重试
            else:
                logger.error("Milvus 客户端多次连接失败，向量检索将不可用")
                return None

# 初始化 Embedding 客户端 (智谱AI)
embedding_client = OpenAI(
    api_key=EMBEDDING_API_KEY,
    base_url=EMBEDDING_BASE_URL
)

def vector_search(query: str, tenant_id: int, top_k: int = 5) -> list:
    """
    执行 Milvus 向量检索
    """
    milvus_client = get_milvus_client()
    if not milvus_client:
        logger.warning("Milvus 不可用，跳过向量检索")
        return []
    
    collection_name = f"tenant_{tenant_id}"
    
    # 检查集合是否存在
    if collection_name not in milvus_client.list_collections():
        logger.warning(f"Milvus 集合 {collection_name} 不存在")
        return []

    try:
        # 1. 将用户提问向量化（调用智谱 API）
        response = embedding_client.embeddings.create(
            model="embedding-3",
            input=[query],
            dimensions=1024
        )
        query_vector = response.data[0].embedding

        # 2. 去 Milvus 检索
        # metric_type 为 IP (内积)，所以 distance 越大越相似
        search_res = milvus_client.search(
            collection_name=collection_name,
            data=[query_vector],
            limit=top_k,
            output_fields=["text", "document_id"] 
        )

        # 3. 整理返回结果格式
        results = []
        # search_res 是一个列表，里面的第一个元素是查询的返回结果
        if search_res:
            for hit in search_res[0]:
                results.append({
                    "chunk_id": hit['id'], 
                    "content": hit['entity']['text'],
                    "vector_score": hit['distance'] # Milvus 返回的相似度得分
                })
        return results
    except Exception as e:
        logger.error(f"向量检索失败: {e}")
        return []