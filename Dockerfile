FROM python:3.11-slim

# 安装中文字体：PDF 导出依赖 CJK 字体，slim 镜像默认不含
# --no-install-recommends 避免拉入无关包，装完清理 apt 索引以减小镜像
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

EXPOSE 8000

CMD ["python", "main.py", "serve"]
