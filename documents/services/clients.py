# clients.py
# 功能：懒加载初始化Milvus向量库客户端、Embedding客户端，全局单例复用
# 说明：Milvus采用懒加载（第一次调用get_milvus_client时才创建连接）；Embedding客户端模块加载时直接初始化
# 下游：文档解析任务写入向量、问答检索时调用

# 1. Python内置标准库
import os
import logging

# 2. 第三方库
from openai import OpenAI
from pymilvus import MilvusClient

logger = logging.getLogger(__name__)

# Milvus全局单例变量
_milvus_client = None


def get_milvus_client():
    """
    懒加载获取Milvus客户端，全局单例，避免重复创建连接
    :return: MilvusClient实例，失败返回None
    """
    global _milvus_client
    if _milvus_client is None:
        try:
            _milvus_client = MilvusClient(uri=os.getenv("MILVUS_URI", "http://milvus-standalone:19530"))
            logger.info("成功初始化 Milvus 客户端")
        except Exception as e:
            logger.error(f"初始化 Milvus 客户端失败: {e}")
            return None
    return _milvus_client


# 模块加载时直接初始化Embedding客户端（智谱Embedding，兼容OpenAI SDK）
try:
    embedding_client = OpenAI(
        api_key=os.getenv("EMBEDDING_API_KEY"),
        base_url=os.getenv("EMBEDDING_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
    )
    logger.info("成功初始化 Embedding 客户端")

except Exception as e:
    logger.error(f"初始化 Embedding 客户端失败: {e}")
    embedding_client = None
