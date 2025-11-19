import json
import logging
import os
import random
from typing import Any, Dict, Optional
from groq import Groq
from google import genai
from google.genai import types

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GENAI_API_KEY = os.environ.get("GENAI_API_KEY")

logger = logging.getLogger(__name__)

AI_MODELS = [
    {
        "name": "gemma-3-27b-it",
        "day_limit_requests": 14400,
        "is_visual": True,
        "provider": "Google",
        "type": "premium"
    },
    {
        "name": "gemma-3-12b-it",
        "day_limit_requests": 14400,
        "is_visual": False,
        "provider": "Google",
        "type": "basic"
    },
    {
        "name": "gemini-2.0-flash-lite",
        "day_limit_requests": 1500,
        "is_visual": False,
        "provider": "Google",
        "type": "premium"
    },
    {
        "name": "gemini-2.0-flash",
        "day_limit_requests": 1500,
        "is_visual": False,
        "provider": "Google",
        "type": "premium"
    },
    {
        "name": "llama-3.3-70b-versatile",
        "day_limit_requests": 1000,
        "is_visual": False,
        "provider": "Groq",
        "type": "premium"
    },
    {
        "name": "qwen/qwen3-32b",
        "day_limit_requests": 1000,
        "is_visual": False,
        "provider": "Groq",
        "type": "premium"
    },
]

genai_client = genai.Client(api_key=GENAI_API_KEY)
try:
    client = Groq(api_key=GROQ_API_KEY)
except TypeError:
    import httpx
    client = Groq(
        api_key=GROQ_API_KEY,
        http_client=httpx.Client()
    )


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """
    Пытается вытащить JSON из текста (находит первую { и последнюю }).
    """
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == -1:
            return None
        return json.loads(text[start:end])
    except Exception as e:
        logger.warning(f"JSON parse error: {e}")
        return None


def generate_text(query: str, max_tokens: int = 2500, temperature: float = 0.7, top_p: float = 0.9) -> Dict[str, Any]:
    """
    Универсальный генератор JSON-ответа через Google или Groq.
    Возвращает словарь (dict), который можно сразу использовать.
    Чередует модели из AI_MODELS.
    """
    models = AI_MODELS.copy()
    random.shuffle(models)

    for model_cfg in models:
        model = model_cfg["name"]
        provider = model_cfg["provider"]

        for attempt in range(2):
            try:
                if provider == "Google":
                    gen_config = types.GenerateContentConfig(
                        temperature=temperature,
                        top_p=top_p,
                        max_output_tokens=max_tokens,
                    )
                    response = genai_client.models.generate_content(
                        model=model,
                        contents=[query],
                        config=gen_config,
                    )
                    text = response.text

                elif provider == "Groq":
                    msgs = [{"role": "user", "content": query}]
                    completion = client.chat.completions.create(
                        messages=msgs,
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p,
                    )
                    text = completion.choices[0].message.content

                else:
                    logger.error(f"❌ Unknown provider: {provider}")
                    continue

                parsed = extract_json(text)

                if parsed is None:
                    logger.warning(f"⚠️ Could not extract JSON on attempt {attempt + 1} with {model}")
                    continue

                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, str):
                    try:
                        return json.loads(parsed)
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to parse JSON string from model {model}: {e}")
                        continue

                logger.warning(f"⚠️ Unexpected parsed type from model {model}: {type(parsed)}")

            except Exception as e:
                logger.error(f"❌ Error with model {model} (attempt {attempt + 1}): {e}")

    raise RuntimeError("All models failed to generate valid JSON")
