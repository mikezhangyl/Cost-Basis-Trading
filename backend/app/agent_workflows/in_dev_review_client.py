import os
from typing import Protocol


class InDevReviewClient(Protocol):
    def review(self, payload: dict) -> dict:
        ...


class StaticInDevReviewClient:
    def __init__(self, report: str) -> None:
        self.report = report

    def review(self, payload: dict) -> dict:
        return {
            "report": self.report,
            "findings": [],
            "summary": "Static in-dev review completed.",
        }


class DeepSeekInDevReviewClient:
    def __init__(self, client: object, model: str = "deepseek-v4-pro") -> None:
        self.client = client
        self.model = model

    @classmethod
    def from_environment(cls) -> "DeepSeekInDevReviewClient | None":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return None
        try:
            from openai import OpenAI
        except ImportError:
            return None
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=90.0, max_retries=0)
        langsmith_key = _langsmith_api_key()
        tracing_setting = os.getenv("LANGSMITH_TRACING")
        tracing_enabled = bool(langsmith_key) and (tracing_setting is None or tracing_setting.lower() == "true")
        if tracing_enabled:
            os.environ.setdefault("LANGSMITH_API_KEY", str(langsmith_key))
            os.environ.setdefault("LANGSMITH_TRACING", "true")
            os.environ.setdefault("LANGSMITH_PROJECT", "cost-basis-trading-dev")
            from langsmith.wrappers import wrap_openai

            client = wrap_openai(client)
        return cls(client)

    def review(self, payload: dict) -> dict:
        prompt = _build_prompt(payload)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个开发期质量审核 agent，只审核 report 与计划的一致性，不给投资建议。"},
                {"role": "user", "content": prompt},
            ],
            stream=False,
            timeout=90.0,
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}},
        )
        content = response.choices[0].message.content
        return {
            "report": content,
            "findings": [],
            "summary": _first_line(content),
        }


def _build_prompt(payload: dict) -> str:
    return (
        "请对以下 research run 产物做开发期审核。\n"
        "目标：比较计划文档与实际 report/artifacts 是否一致。\n"
        "要求：\n"
        "1. 不给真实投资建议。\n"
        "2. 明确列出符合项、偏离项、bug、文档口径问题、需要用户决策的问题。\n"
        "3. 特别检查观察点、N/A、scoring、future-leak、artifact 完整性。\n"
        "4. 最后给出修复计划草案，但说明必须等待用户 approval。\n\n"
        f"PAYLOAD:\n{payload}"
    )


def _first_line(content: str) -> str:
    for line in content.splitlines():
        if line.strip():
            return line.strip()
    return "In-dev review completed."


def _langsmith_api_key() -> str | None:
    return os.getenv("LANGSMITH_API_KEY")
