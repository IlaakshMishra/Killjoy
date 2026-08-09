import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain_aws import ChatBedrockConverse

from generator import generate_tests

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
    diff = payload.get("diff", "")
    env_map = payload.get("env_map", {})
    surviving_mutants = payload.get("surviving_mutants")
    whole_repo_files = payload.get("whole_repo_files")

    if not diff:
        return {"error": "diff is required"}

    try:
        test_source = generate_tests(diff, env_map, _llm_invoke, surviving_mutants, whole_repo_files)
    except SyntaxError as exc:
        logger.error("Generated test source failed to compile: %s", exc)
        return {"error": f"generated test source is not valid Python: {exc}"}

    return {"test_source": test_source}


if __name__ == "__main__":
    app.run()
