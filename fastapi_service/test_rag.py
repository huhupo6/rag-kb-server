import requests

API_URL = "http://localhost:9000/api/chat_test"
TENANT_ID = 3

TEST_CASES = [
    {
        "id": "TC05", "type": "语义联想与惩罚机制",
        "query": "员工如果连续两个考核周期表现极差，公司会怎么处理？",
        "expected_keywords": [["降级"], ["降薪"], ["中位数"]]
    },
]

# 检查关键词组，只要每个子列表里有一个词命中即可
def check_keywords(answer, keyword_groups):
    for group in keyword_groups:
        if not any(kw in answer for kw in group):
            return False, group
    return True, None

def run_tests():
    pass_count = 0
    total_count = len(TEST_CASES)

    print(f"================ 开始 RAG 自动化测试 (共 {total_count} 题) ================\n")

    for case in TEST_CASES:
        print(f"[{case['id']}] {case['type']} - {case['query']}")
        
        try:
            # 发起非流式请求
            response = requests.post(API_URL, json={"tenant_id": TENANT_ID, "query": case["query"]}, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            answer = data["answer"]
            # 打印截断的答案，避免刷屏
            print(f"  模型回答: {answer[:80].replace(chr(10), ' ')}...")
            
            # 断言逻辑
            is_pass, missing_group = check_keywords(answer, case["expected_keywords"])
            
            if is_pass:
                print(f"  🟢 结果: PASS (命中上下文块: {data['cited_chunks_count']})\n")
                pass_count += 1
            else:
                print(f"  🔴 结果: FAIL (缺少关键词: {missing_group})\n")
                
        except Exception as e:
            print(f"  ❌ 请求异常: {e}\n")

    print(f"================ 评测结束 ================")
    print(f"通过: {pass_count} / {total_count} | 准确率: {(pass_count/total_count)*100:.1f}%")

if __name__ == "__main__":
    run_tests()
