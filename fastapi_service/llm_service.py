import os
import logging
from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

logger = logging.getLogger(__name__)

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

# 调用 DeepSeek 流式输出
def generate_stream(query: str, contexts: list):
    if not contexts:
        yield "抱歉，知识库中未找到相关内容，请联系管理员补充。"
        return

    # 拼装上下文
    context_text = "\n\n".join([f"【资料{i+1}】\n{ctx['content']}" for i, ctx in enumerate(contexts)])

    system_prompt = """你是一个严谨的企业知识库助手。
请严格遵守以下规则：
1. 仅根据【资料片段】回答用户问题。
2. 如果资料片段中没有明确的数字、日期或具体结论，请直接回复：“知识库中未找到明确规定，请联系企业管理员”。
3. 严禁根据外部常识推测或编造。"""

    user_prompt = f"【资料片段】\n{context_text}\n\n【用户问题】\n{query}"

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            stream=True
        )

        for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content
                
    except Exception as e:
        logger.error(f"大模型调用失败: {e}")
        yield f"\n[AI 服务异常: {str(e)}]"

# 用于测试的非流式生成接口
def generate_sync(query: str, contexts: list) -> str:
    if not contexts:
        return "知识库中未找到相关规定，请联系企业管理员。"

    context_text = "\n\n".join([f"【资料{i+1}】\n{ctx['content']}" for i, ctx in enumerate(contexts)])

    system_prompt = """你是一个严谨的企业知识库助手。
请严格遵守以下规则：
1. 仅根据【资料片段】回答用户问题。
2. 如果资料片段中没有明确的数字、日期或具体结论，请直接回复：“知识库中未找到明确规定，请联系企业管理员”。
3. 严禁根据外部常识推测或编造。"""

    user_prompt = f"【资料片段】\n{context_text}\n\n【用户问题】\n{query}"

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=800
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"测试接口报错: {str(e)}"
