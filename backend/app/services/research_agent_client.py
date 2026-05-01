import os
from typing import Protocol

from app.core.config import load_environment


class ResearchAgentClient(Protocol):
    def analyze_research_run(self, payload: dict) -> dict:
        ...


class DeepSeekResearchAgentClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-pro",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    @classmethod
    def from_environment(cls) -> "DeepSeekResearchAgentClient | None":
        load_environment()
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return None
        return cls(api_key=api_key)

    def analyze_research_run(self, payload: dict) -> dict:
        try:
            from openai import OpenAI
        except ImportError as error:
            return {
                "status": "failed",
                "model": self.model,
                "review_summary": "OpenAI SDK is not installed, so the AI research agent was not executed.",
                "final_report": "AI report was not generated because the OpenAI SDK is missing.",
                "agent_decisions": [],
                "error": str(error),
            }

        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=60.0)
        langsmith_key = _langsmith_api_key()
        tracing_setting = os.getenv("LANGSMITH_TRACING")
        tracing_enabled = bool(langsmith_key) and (tracing_setting is None or tracing_setting.lower() == "true")
        if tracing_enabled:
            os.environ.setdefault("LANGSMITH_API_KEY", str(langsmith_key))
            os.environ.setdefault("LANGSMITH_TRACING", "true")
            os.environ.setdefault("LANGSMITH_PROJECT", "cost-basis-trading-dev")
            from langsmith.wrappers import wrap_openai

            client = wrap_openai(client)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个严谨的股票筹码因子研究复核 agent。"
                        "你只能基于用户给出的 artifact 摘要做研究复核，不提供个股投资建议，"
                        "必须区分事实、假设、风险和下一步实验。"
                    ),
                },
                {
                    "role": "user",
                    "content": _build_user_prompt(payload),
                },
            ],
            stream=False,
            timeout=60.0,
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}},
        )
        content = response.choices[0].message.content or ""
        return {
            "status": "completed",
            "model": self.model,
            "review_summary": _first_non_empty_line(content),
            "final_report": content,
            "agent_decisions": [
                {
                    "agent": "critic-report-agent",
                    "decision_type": "run_review_and_report",
                    "reasoning_summary": _first_non_empty_line(content),
                }
            ],
        }


def _build_user_prompt(payload: dict) -> str:
    return (
        "请复核以下单股票、多起始日期、多候选策略的研究运行摘要。\n"
        "输出要求：\n"
        "1. 用中文简短总结本次 run。\n"
        "2. 检查是否有未来函数风险。\n"
        "3. 评价候选策略表现，但不要给出真实投资建议。\n"
        "4. 指出样本量、数据质量、confidence 的局限。\n"
        "5. 给出下一轮实验建议。\n\n"
        f"RUN_PAYLOAD:\n{payload}"
    )


def _first_non_empty_line(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "AI research review completed."


def _langsmith_api_key() -> str | None:
    return os.getenv("LANGSMITH_API_KEY")
