FROM python:3.11-slim
WORKDIR /app

# 先复制本地torch离线包
COPY torch-2.1.2+cpu-cp311-cp311-linux_x86_64.whl .
# 离线安装torch
RUN pip install --no-cache-dir torch-2.1.2+cpu-cp311-cp311-linux_x86_64.whl

# 再安装剩下依赖（包含新增的sentencepiece）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    --timeout 120 \
    --retries 5

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
