import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain_aws import ChatBedrockConverse

from scanner import scan_repo
from synthesizer import synthesize_env_map

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")

llm = ChatBedrockConverse(model_id=MODEL_ID)

app = BedrockAgentCoreApp()


def _llm_invoke(prompt: str) -> str:
    response = llm.invoke([{"role": "user", "content": prompt}])
    return response.content if hasattr(response, "content") else str(response)


@app.entrypoint
async def handler(payload: dict) -> dict:
    repo_files = payload.get("repo_files")
    if not repo_files:
        return {"error": "repo_files is required"}

    tmpdir = tempfile.mkdtemp(prefix="killjoy-env-mapper-")
    try:
        for rel_path, content in repo_files.items():
            dest = Path(tmpdir, rel_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)

        raw_scan = scan_repo(Path(tmpdir))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    try:
        env_map = synthesize_env_map(raw_scan, _llm_invoke)
    except ValueError as exc:
        logger.error("Environment Mapper synthesis failed: %s", exc)
        return {"error": str(exc)}

    return env_map


if __name__ == "__main__":
    app.run()
