FROM --platform=linux/arm64 python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml guardrails.py pipeline.py main.py ./
RUN pip install --no-cache-dir -e .
EXPOSE 8080
CMD ["python", "main.py"]
