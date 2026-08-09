FROM --platform=linux/arm64 python:3.13-slim
WORKDIR /app
COPY pyproject.toml runner.py mutation.py sandbox.py main.py ./
COPY vendor_wheels ./vendor_wheels
RUN pip install --no-cache-dir -e .
EXPOSE 8080
CMD ["python", "main.py"]
