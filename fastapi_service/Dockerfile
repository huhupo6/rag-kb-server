# Dockerfile.fastapi
# FastAPI 微服务镜像构建脚本
# 用于向量检索、RRF 融合、检索接口，独立于 Django 主后端
# 使用 uvicorn 启动，容器内监听 9000 端口
# 由主编排文件拉起，供主业务调用检索能力

# 使用官方 Python 3.11 镜像
FROM python:3.11-slim-bookworm

# 设置容器内的工作目录为 /app
WORKDIR /app

# 设置环境变量，防止 Python 写 pyc 文件
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 阿里云源
RUN rm -f /etc/apt/sources.list.d/debian.sources && \
    echo "deb http://mirrors.aliyun.com/debian/ bookworm main non-free non-free-firmware contrib" > /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security/ bookworm-security main" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian/ bookworm-updates main non-free non-free-firmware contrib" >> /etc/apt/sources.list

# 先把本地的 torch 压缩包复制进容器
COPY fastapi_service/torch-2.1.2+cpu-cp311-cp311-linux_x86_64.whl /app/

# 优先安装本地 torch 文件
RUN pip install --no-cache-dir /app/torch-2.1.2+cpu-cp311-cp311-linux_x86_64.whl

# 复制剩下的依赖清单
COPY fastapi_service/requirements.txt /app/
# 使用腾讯云镜像
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    --timeout 120 \
    --retries 5
    
# 复制公共分词包
COPY kb_tokenizer /app/kb_tokenizer

# 只复制 FastAPI 的代码到 /app 下
COPY fastapi_service /app/

# 暴露 FastAPI 服务的端口
EXPOSE 9000

# 启动 Uvicorn 服务器
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]