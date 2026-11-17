import requests
import json
import os
from openai import OpenAI
from dotenv import load_dotenv

# 加载项目根目录 .env
load_dotenv(dotenv_path="../.env")

judge_client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

API_URL = "http://localhost:9000/api/chat_test"
TENANT_ID = 3
USER_ID = 1

TEST_CASES = [
    {
        "query": "星辰科技员工薪资由哪几部分组成？",
        "ground_truth": "员工薪资由基本工资、绩效工资、岗位津贴三部分组成。",
    },
    {
        "query": "P5‑P6高级专员年终奖是几个月月薪？",
        "ground_truth": "P5‑P6高级专员年终奖基数为3个月月薪。",
    },
    {
        "query": "星辰科技每年什么时候做全员年度普调？",
        "ground_truth": "每年4月进行全员年度调薪。",
    },
    {
        "query": "公司营收增长超过20%，全员基础薪资普调多少？",
        "ground_truth": "若公司年度营收增长超过20%，全员基础薪资普调5%。",
    },
    {
        "query": "晋升调薪和年度普调在同一年可以叠加吗？",
        "ground_truth": "晋升调薪与年度普调在同一年度内不叠加，取较高涨幅执行。",
    },
    {
        "query": "什么情况会触发员工降级降薪？",
        "ground_truth": "若员工连续两个考核周期绩效为D，将触发降级流程，薪资下调至新职级对应区间的中位数。",
    },
    {
        "query": "员工晋升需要满足什么基础任职条件？",
        "ground_truth": "员工晋升需满足在当前职级连续任职满一年，且近两次绩效考核达到规定标准。",
    },
    # 幻觉测试：文档不存在该知识点，RAG应当回答无相关资料，不能编造
    {
        "query": "连续3天旷工怎么处理？",
        "ground_truth": "文档中没有关于旷工处理的相关制度。",
    }
]

def evaluate_answer(query: str, ground_truth: str, rag_answer: str):
    prompt = """你是公正的RAG评测裁判，依据标准答案对AI回答打分，分数区间0-10。
打分规则：
10分：答案完全匹配标准答案，无幻觉，信息完整。
7-9分：核心事实正确，少量细节缺失，无错误编造。
4-6分：部分事实正确，存在少量错误。
0-3分：答非所问、严重事实错误、编造文档不存在的信息（幻觉）。

【用户问题】{query}
【标准答案】{ground_truth}
【RAG生成答案】{rag_answer}

严格只输出JSON，不要额外文字：
{{"score": 数字, "reason": "简要评分理由"}}
"""
    # 填充变量
    final_prompt = prompt.format(
        query=query,
        ground_truth=ground_truth,
        rag_answer=rag_answer
    )

    resp = judge_client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": final_prompt}],
        temperature=0.0,
        response_format={"type": "json_object"}
    )
    content = resp.choices[0].message.content.strip()
    return json.loads(content)

if __name__ == "__main__":
    total_score = 0
    case_count = len(TEST_CASES)
    print("====== LLM‑as‑Judge 评测开始 ======\n")
    for idx, case in enumerate(TEST_CASES, 1):
        print(f"【用例 {idx}/{case_count}】问题：{case['query']}")
        try:
            r = requests.post(
                API_URL,
                json={"tenant_id": TENANT_ID, "user_id": USER_ID, "query": case["query"]},
                timeout=30
            )
            r.raise_for_status()
            rag_ans = r.json().get("answer", "")
            print(f"RAG输出：{rag_ans}\n")

            res = evaluate_answer(case["query"], case["ground_truth"], rag_ans)
            print(f"得分：{res['score']} | 理由：{res['reason']}\n")
            total_score += res["score"]

        except Exception as e:
            print(f"!!!接口异常：{e}\n")

    avg = total_score / case_count
    print(f"====== 评测结束，平均得分：{avg:.2f} /10 ======")