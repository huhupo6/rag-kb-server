import os
import logging
from openai import OpenAI
from pymilvus import MilvusClient

logger = logging.getLogger(__name__)

# 1. 全局初始化 Milvus 客户端
try:
    milvus_client = MilvusClient(uri=os.getenv("MILVUS_URI", "http://milvus-standalone:19530"))
    logger.info("成功初始化 Milvus 客户端")
except Exception as e:
    logger.error(f"初始化 Milvus 客户端失败: {e}")
    milvus_client = None

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