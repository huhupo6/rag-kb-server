import requests
import json
import os
from openai import OpenAI
from dotenv import load_dotenv

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
        "query": "连续3天旷工怎么处理？",
        "ground_truth": "予以开除处理。",
    },
]

def evaluate_answer(query, ground_truth, rag_answer):
    prompt = f"""你是一个公正的裁判。请根据标准答案，对AI生成的答案进行打分（0-10分）。
    - 完全符合且没有幻觉：10分
    - 核心意思正确但有少许偏差：7-9分
    - 答非所问或存在严重幻觉：0-3分

    【问题】{query}
    【标准答案】{ground_truth}
    【AI答案】{rag_answer}
    
    请直接输出 JSON 格式：{{"score": 分数, "reason": "评分理由"}}"""

    response = judge_client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)

if __name__ == "__main__":
    total_score = 0
    print("================ 开始 LLM-as-Judge 深度评测 ================\n")
    for case in TEST_CASES:
        res = requests.post(API_URL, json={
            "tenant_id": TENANT_ID, "user_id": USER_ID, "query": case["query"]
        })
        rag_answer = res.json().get("answer", "")
        
        result = evaluate_answer(case["query"], case["ground_truth"], rag_answer)
        print(f"问题: {case['query']}")
        print(f"打分: {result['score']} | 理由: {result['reason']}\n")
        total_score += result['score']
        
    print(f"================ 平均得分: {total_score / len(TEST_CASES)} / 10 ================")