# 1. Python内置
import os

# 2. 第三方
import redis
from dotenv import load_dotenv

# 加载根目录的 .env 文件
load_dotenv(dotenv_path="../.env")

# MySQL 配置
DB_HOST = os.getenv('DB_HOST', 'db')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')

# Redis 配置
REDIS_HOST = os.getenv('REDIS_HOST', 'redis')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_DB = int(os.getenv('REDIS_DB', 0))

# 全局单例 Redis 客户端
redis_client = redis.Redis(
    host=REDIS_HOST, 
    port=REDIS_PORT, 
    db=REDIS_DB, 
    decode_responses=True
)

# Milvus 向量库配置
MILVUS_URI = os.getenv("MILVUS_URI", "http://milvus-standalone:19530")

# 智谱 Embedding 配置
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")

# DeepSeek LLM 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# Rerank 微服务配置
RERANK_URL = os.getenv("RERANK_URL", "http://rerank-service:8000/rerank")
RERANK_TIMEOUT = int(os.getenv("RERANK_TIMEOUT", "20"))
