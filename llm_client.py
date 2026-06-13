"""
LLM 客户端模块
支持多个 LLM 提供商（DeepSeek / Polo API），内置自动重试和 validator 校验。
"""
import json
import time
from openai import OpenAI
import config


class LLMFailure(Exception):
    """LLM 调用在允许的重试次数内未能获得有效结果"""
    def __init__(self, caller_id: str, attempts: int, last_error: str):
        self.caller_id = caller_id
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"[{caller_id}] LLM 调用失败 {attempts} 次: {last_error}")


class LLMClient:
    """LLM 客户端，基于 OpenAI SDK，支持多提供商"""

    def __init__(self, provider_config: dict = None):
        """
        初始化 LLM 客户端。

        Args:
            provider_config: 提供商配置字典，包含 api_key, model, base_url。
                            为 None 时使用 config 中激活的提供商。
        """
        if provider_config is None:
            provider_config = config.AVAILABLE_MODELS[
                "1" if config.ACTIVE_PROVIDER == "deepseek" else "2"
            ]

        api_key = provider_config.get("api_key")
        self.model = provider_config.get("model", "unknown")
        base_url = provider_config.get("base_url", "")
        provider = provider_config.get("provider", "unknown")

        if not api_key:
            raise RuntimeError(
                f"[{provider}] API Key 未配置，请检查对应配置文件或环境变量。"
            )

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=config.LLM_CALL_TIMEOUT,
        )
        self.provider = provider

    def query(self, system_prompt: str, user_content: str, json_mode: bool = True, think: bool = False):
        """
        单次调用 LLM，成功返回解析后的结果，失败抛出异常。
        """
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
        }

        # DeepSeek 支持原生 thinking 参数；Polo API 可能不支持
        if self.provider == "deepseek":
            kwargs["extra_body"] = {
                'thinking': {'type': 'enabled' if think else 'disabled'}
            }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content

        if json_mode or self._looks_like_json(content):
            return self._parse_json(content, self.provider)
        else:
            return content

    def _looks_like_json(self, content: str) -> bool:
        """判断内容是否看起来像 JSON（含 markdown 包裹的 JSON）"""
        stripped = content.strip()
        return stripped.startswith("{") or stripped.startswith("[") or \
               stripped.startswith("```json") or stripped.startswith("```")

    def _parse_json(self, content: str, caller_id: str = "Unknown"):
        """
        尝试解析 JSON，自动去除 markdown 代码块包裹。

        Args:
            content: 可能包含 markdown 包裹的 JSON 字符串
            caller_id: 调用者标识，用于日志

        Returns:
            解析后的 Python 对象

        Raises:
            json.JSONDecodeError: 解析失败
        """
        cleaned = content.strip()

        # 去除 ```json ... ``` 或 ``` ... ``` 包裹
        if cleaned.startswith("```"):
            # 移除开头的 ```json 或 ```
            first_newline = cleaned.find("\n")
            if first_newline != -1:
                cleaned = cleaned[first_newline + 1:]
            # 移除结尾的 ```
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # 尝试修复常见问题后重试
            print(f"[LLM][{caller_id}] JSON 解析失败，原始内容: {repr(content[:200])}")
            raise

    def call_with_retry(self, system_prompt: str, user_content: str,
                        json_mode: bool = True, validator=None,
                        retry_delay: float = 2.0, think: bool = False,
                        caller_id: str = "Unknown",
                        max_retries: int = None):
        """
        调用 LLM 直到成功（通过 validator 校验）或超过最大重试次数。

        Args:
            system_prompt: 系统提示词
            user_content: 用户消息
            json_mode: 是否启用 JSON 模式
            validator: 可选的验证函数，接受 LLM 返回值，通过返回 True
            retry_delay: 重试间隔（秒）
            think: 是否启用思考模式
            caller_id: 调用者标识，用于日志追踪
            max_retries: 最大尝试次数，None 时取 config.LLM_MAX_RETRIES

        Returns:
            LLM 返回结果（已通过 validator 校验）

        Raises:
            LLMFailure: 超过最大尝试次数仍未获得有效结果
        """
        if max_retries is None:
            max_retries = config.LLM_MAX_RETRIES

        last_error = "unknown"
        for attempt in range(1, max_retries + 1):
            try:
                result = self.query(system_prompt, user_content, json_mode=json_mode, think=think)

                if validator and not validator(result):
                    last_error = "返回结果未通过验证"
                    print(f"[LLM][{caller_id}] {last_error}（第 {attempt}/{max_retries} 次），{retry_delay}秒后重试...")
                else:
                    return result

            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                print(f"[LLM][{caller_id}] 调用失败: {last_error}（第 {attempt}/{max_retries} 次）。{retry_delay}秒后重试...")

            if attempt < max_retries:
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)

        raise LLMFailure(caller_id, max_retries, last_error)


if __name__ == "__main__":
    client = LLMClient()
    print("正在测试 LLM 客户端...")
    try:
        resp = client.call_with_retry(
            "你是一个乐于助人的助手。请输出 JSON。",
            '用 JSON 格式说你好，使用 "message" 键。',
            json_mode=True,
            validator=lambda x: 'message' in x,
        )
        print(f"测试成功: {resp}")
    except Exception as e:
        print(f"测试失败: {e}")
