# 初始化 Embedding 客户端、Milvus 向量数据库客户端

import os
import logging
from openai import OpenAI
from pymilvus import MilvusClient

logger = logging.getLogger(__name__)

_milvus_client = None

# 1. 懒加载 Milvus 客户端
def get_milvus_client():
    global _milvus_client
    if _milvus_client is None:
        try:
            _milvus_client = MilvusClient(uri=os.getenv("MILVUS_URI", "http://milvus-standalone:19530"))
            logger.info("成功初始化 Milvus 客户端")
        except Exception as e:
            logger.error(f"初始化 Milvus 客户端失败: {e}")
            return None
    return _milvus_client

# 2. 全局初始化 Embedding 客户端
try:
    embedding_client = OpenAI(
        api_key=os.getenv("EMBEDDING_API_KEY"),
        base_url=os.getenv("EMBEDDING_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
    )
    logger.info("成功初始化 Embedding 客户端")
    
except Exception as e:
    logger.error(f"初始化 Embedding 客户端失败: {e}")
    embedding_client = None