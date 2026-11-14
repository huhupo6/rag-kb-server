# BM25 全文检索 文件

# 1. Python内置
import logging
import re

# 2. 第三方
import jieba
import pymysql
from rank_bm25 import BM25Okapi

# 3. 当前项目配置
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME

logger = logging.getLogger(__name__)

# jieba 分词预处理
def tokenize_query(text: str) -> list:
    # 正则：匹配整数/小数，后面可选跟随 %、天、个、月、年、元、号、日
    pattern = r'\d+(?:\.\d+)?[%天个月元年号日]?'
    # 提取文本中所有匹配到的数字+单位片段
    matches = re.findall(pattern, text)

    for m in matches:
        # 强制将数字+单位组合加入词典
        jieba.add_word(m)

    return jieba.lcut(text)

# 多租户 BM25 检索
class TenantBM25Engine:
    def __init__(self, tenant_id: int):
        self.tenant_id = tenant_id
        self.bm25 = None
        self.chunk_ids = []
        self.chunks_content = []
        self._load_from_mysql()

    def _load_from_mysql(self):
        try:
            conn = pymysql.connect(
                host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
                database=DB_NAME, charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
            with conn.cursor() as cursor:
                sql = """
                    SELECT dc.id, dc.content_tokenized, dc.content 
                    FROM document_chunks dc
                    JOIN documents d ON dc.document_id = d.id
                    WHERE d.tenant_id = %s AND d.is_deleted = 0
                """
                cursor.execute(sql, (self.tenant_id,))
                rows = cursor.fetchall()
            conn.close()

            if not rows:
                logger.warning(f"租户 {self.tenant_id} 暂无可用文档块，BM25 索引为空。")
                return

            corpus = []
            for row in rows:
                tokens = row['content_tokenized'].split()
                corpus.append(tokens)
                self.chunk_ids.append(row['id'])
                self.chunks_content.append(row['content'])

            self.bm25 = BM25Okapi(corpus)
            logger.info(f"✅  租户 {self.tenant_id} 的 BM25 索引构建成功，共 {len(corpus)} 个文本块。")
            
        except Exception as e:
            logger.error(f"构建 BM25 索引失败: {e}")

    def search(self, query: str, top_k: int = 5) -> list:
        if not self.bm25:
            return []
        
        tokenized_query = tokenize_query(query)
        scores = self.bm25.get_scores(tokenized_query)
        
        top_n = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        
        results = []
        for idx in top_n:
            if scores[idx] > 0:
                results.append({
                    "chunk_id": self.chunk_ids[idx],
                    "content": self.chunks_content[idx],
                    "bm25_score": float(scores[idx])
                })
        return results

# 全局单例缓存池
bm25_engines = {}

def get_bm25_engine(tenant_id: int) -> TenantBM25Engine:
    if tenant_id not in bm25_engines:
        bm25_engines[tenant_id] = TenantBM25Engine(tenant_id)
    return bm25_engines[tenant_id]

# 清除指定租户的 BM25 缓存
def clear_bm25_engine(tenant_id: int):
    if tenant_id in bm25_engines:
        del bm25_engines[tenant_id]
        logger.info(f"已清理租户 {tenant_id} 的 BM25 内存缓存")