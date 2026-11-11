import os
from dotenv import load_dotenv

# 加载根目录的 .env 文件
load_dotenv(dotenv_path="../.env")

# MySQL 配置
DB_HOST = os.getenv('DB_HOST', 'db')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')

# Milvus 配置
MILVUS_URI = os.getenv("MILVUS_URI", "http://milvus-standalone:19530")

# 智谱 (Embedding) 配置
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")

# DeepSeek (LLM) 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"