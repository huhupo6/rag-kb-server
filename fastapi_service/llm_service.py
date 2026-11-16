# 1. Python内置
import logging
import json

# 2. 第三方
from openai import OpenAI

# 3. 当前项目配置
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

logger = logging.getLogger(__name__)

# 初始化DeepSeek客户端，兼容OpenAI SDK
client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL
)


def generate_stream(query: str, contexts: list):
    """
    流式调用DeepSeek大模型，RAG问答流式输出
    规则：严格基于检索到的知识库片段回答，禁止编造内容
    :param query: 用户原始提问
    :param contexts: 检索回来的文档块列表，每个元素包含content字段
    :yield: 逐块返回回答文本；最后携带 __USAGE__ 标记的token统计信息
    """
    if not contexts:
        yield "抱歉，知识库中未找到相关内容，请联系管理员补充。"
        return

    # 拼接检索到的参考资料片段
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
            stream=True,
            stream_options={"include_usage": True}
        )

        usage_data = None
        for chunk in response:
            # 输出回答文本片段
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                yield content

            # 捕获末尾返回的token消耗统计
            if hasattr(chunk, 'usage') and chunk.usage:
                usage_data = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens
                }
        # 流式分片遍历完成后，推送token消耗标识，上游解析后用于入库、计费
        if usage_data:
            yield f"__USAGE__{json.dumps(usage_data)}"

    except Exception as e:
        logger.error(f"大模型流式调用失败: {e}")
        yield f"\n[AI 服务异常: {str(e)}]"


def generate_sync(query: str, contexts: list) -> tuple[str, dict]:
    """
    同步版本RAG问答接口，用于调试、测试场景
    :param query: 用户原始提问
    :param contexts: 检索回来的文档块列表
    :return: (回答文本, token消耗字典)，供上层保存聊天记录、统计用量
    """
    if not contexts:
        return "知识库中未找到相关规定，请联系企业管理员。", {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }

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
        answer = response.choices[0].message.content
        usage_data = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
        return answer, usage_data
    except Exception as e:
        logger.error(f"大模型同步调用失败: {e}")
        return f"测试接口报错: {str(e)}", {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }
