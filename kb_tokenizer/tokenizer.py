import re
import jieba

def tokenize_text(text: str) -> list:
    """
    统一分词器（供 Django 入库与 FastAPI 查询共同使用）
    - 保护数字与单位不被切碎（例如：3天、100%）
    - 不污染 Jieba 全局词典，实现租户级隔离
    """
    if not text:
        return []

    # 1. 正常分词
    tokens = jieba.lcut(text)
    
    merged_tokens = []
    skip_next = False
    
    # 2. 后置合并：检测纯数字 + 紧跟的单位
    for i in range(len(tokens)):
        if skip_next:
            skip_next = False
            continue
            
        current = tokens[i]
        
        if i + 1 < len(tokens):
            next_word = tokens[i+1]
            # 如果当前是数字，且下一个是单位，合并
            if re.fullmatch(r'\d+(?:\.\d+)?', current) and next_word in ['天', '个', '月', '年', '元', '号', '日', '%']:
                merged_tokens.append(current + next_word)
                skip_next = True
                continue
        
        merged_tokens.append(current)
        
    return merged_tokens