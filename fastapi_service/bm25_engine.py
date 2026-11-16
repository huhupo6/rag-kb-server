# 1. Python内置
import logging
import re
import collections

# 2. 第三方
import jieba
import pymysql
import redis
from dbutils.pooled_db import PooledDB
from rank_bm25 import BM25Okapi

# 3. 当前项目配置
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, redis_client

logger = logging.getLogger(__name__)

# 数据库连接池 (解决频繁 TCP 握手开销)
POOL = PooledDB(
    creator=pymysql,
    maxconnections=10,  # 最大连接数
    mincached=1,        # 初始化时创建的连接数
    maxcached=5,        # 连接池中空闲连接的最大数
    blocking=True,      # 连接池满时是否阻塞
    host=DB_HOST,
    user=DB_USER,
    password=DB_PASSWORD,
    database=DB_NAME,
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

def tokenize_query(text: str) -> list:
    """
    后置合并分词：防止污染全局词典，同时保护数字+单位
    示例："3天" 作为一个整体分词，而不是 ["3","天"]
    """
    # 1. 先用默认词典正常分词
    tokens = jieba.lcut(text)

    merged_tokens = []
    skip_next = False

    # 2. 在列表层面进行词组合并
    for i in range(len(tokens)):
        if skip_next:
            skip_next = False
            continue

        current = tokens[i]

        # 如果当前是数字，且下一个是单位，则合并
        if i + 1 < len(tokens):
            next_word = tokens[i+1]
            if re.fullmatch(r'\d+(?:\.\d+)?', current) and next_word in ['天', '个', '月', '年', '元', '号', '日', '%']:
                merged_tokens.append(current + next_word)
                skip_next = True
                continue
        merged_tokens.append(current)
        
    return merged_tokens    

class TenantBM25Engine:
    """
    多租户隔离的BM25检索引擎
    每个租户独立一套BM25索引，从MySQL读取预分词好的文档块
    """
    def __init__(self, tenant_id: int):
        self.tenant_id = tenant_id
        self.bm25 = None          # BM25Okapi实例
        self.chunk_ids = []       # 文档块id列表，和corpus一一对应
        self.chunks_content = []  # 文档块原文，和corpus一一对应
        self._load_from_mysql()

    def _load_from_mysql(self):
        """从MySQL加载当前租户文档块，构建BM25语料"""
        try:
            # 从连接池借用连接
            conn = POOL.connection()

            try:
                with conn.cursor() as cursor:
                    sql = """
                        SELECT dc.id, dc.content_tokenized, dc.content 
                        FROM document_chunks dc
                        JOIN documents d ON dc.document_id = d.id
                        WHERE d.tenant_id = %s AND d.is_deleted = 0
                    """
                    cursor.execute(sql, (self.tenant_id,))
                    rows = cursor.fetchall()
            finally:
                # 归还连接到池中
                conn.close()

            if not rows:
                logger.warning(f"租户 {self.tenant_id} 暂无可用文档块，BM25 索引为空。")
                return

            corpus = []
            for row in rows:
                # content_tokenized 是入库时预先分词、空格拼接好的字符串，直接split
                tokens = row['content_tokenized'].split()
                corpus.append(tokens)
                self.chunk_ids.append(row['id'])
                self.chunks_content.append(row['content'])

            self.bm25 = BM25Okapi(corpus)
            logger.info(f"租户 {self.tenant_id} 的 BM25 索引构建成功，共 {len(corpus)} 个文本块。")

        except Exception as e:
            logger.error(f"构建 BM25 索引失败: {e}")

    def search(self, query: str, top_k: int = 5) -> list:
        """
        BM25检索
        :param query: 用户提问
        :param top_k: 返回topN结果
        :return: [{"chunk_id":xxx, "content":xxx, "bm25_score":xxx}]
        """
        if not self.bm25:
            return []

        tokenized_query = tokenize_query(query)
        scores = self.bm25.get_scores(tokenized_query)

        # 按分数从高到低排序，取前top_k
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


# 全局缓存：LRU，最多缓存 MAX_CACHED_TENANTS 个租户的BM25索引
MAX_CACHED_TENANTS = 5
bm25_engines = collections.OrderedDict()


def get_bm25_engine(tenant_id: int) -> TenantBM25Engine:
    """
    LRU缓存获取租户BM25实例
    存在则刷新访问时间；不存在则新建，满了淘汰最久未访问租户
    """
    # 从 Redis 获取该租户当前的版本号（默认0）
    version_key = f"bm25_version:{tenant_id}"
    current_version = 0

    try:
        # 尝试从 Redis 获取版本号
        current_version = int(redis_client.get(version_key) or 0)
    except Exception as e:
        # 【降级处理】Redis 挂了，无法获取最新版本号
        logger.error(f"Redis 获取版本号失败，启动降级策略: {e}")
        
        # 如果本地已有缓存，直接返回旧缓存，保证检索可用
        if tenant_id in bm25_engines:
            engine, _ = bm25_engines[tenant_id]
            logger.warning(f"Redis 异常，租户 {tenant_id} 使用本地旧缓存服务")
            return engine
        # 如果本地没有缓存，current_version 保持为 0，继续往下走去 MySQL 重建

    # 1. 缓存命中，且版本号一致
    if tenant_id in bm25_engines:
        engine, local_version = bm25_engines[tenant_id]

        # 版本一致，直接返回并刷新访问时间
        if local_version == current_version:
            bm25_engines.move_to_end(tenant_id)
            return engine
        else:
            # 版本不一致，说明其他进程或Django通知了更新，需要重建
            logger.info(f"租户 {tenant_id} 缓存版本 (本地 {local_version} != Redis {current_version})，准备重建...")
            del bm25_engines[tenant_id]

    # 2. 缓存已满，淘汰最久未使用（OrderedDict头部是最久未访问）
    if len(bm25_engines) >= MAX_CACHED_TENANTS:
        oldest_tenant_id, _ = bm25_engines.popitem(last=False)
        logger.info(f"LRU 淘汰：内存限制 {MAX_CACHED_TENANTS} 个，已释放租户 {oldest_tenant_id} 的 BM25 缓存")

    # 3. 新建引擎，并与当前版本号绑定存入缓存
    engine = TenantBM25Engine(tenant_id)
    bm25_engines[tenant_id] = (engine, current_version)
    return engine


def clear_bm25_engine(tenant_id: int):
    """
    清理指定租户BM25缓存（支持 Redis 故障降级）
    """
    version_key = f"bm25_version:{tenant_id}"
    try:
        redis_client.incr(version_key)
        logger.info(f"已通知 Redis 更新租户 {tenant_id} 的 BM25 缓存版本")
    except Exception as e:
        # Redis 挂了，无法广播版本更新
        logger.error(f"Redis 更新版本号失败，尝试直接清理本地缓存: {e}")
        # 虽然其他 Worker 进程可能无法同步，但至少当前进程能清掉
        if tenant_id in bm25_engines:
            del bm25_engines[tenant_id]