"""Test script to verify OpenAI API connection and authentication."""

import argparse

from config_models import DEFAULT_MODEL
from config_models import load_app_config
from utils import get_openai_client


def test_llm_connection() -> str:
    """Send a basic prompt and return the confirmation response."""
    client = get_openai_client()
    cfg = load_app_config("config.yaml")
    model = cfg.model or DEFAULT_MODEL

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant. Reply briefly.",
            },
            {
                "role": "user",
                "content": "Please respond with a short confirmation that the API connection works.",
            },
        ],
    )

    return response.choices[0].message.content or ""


def list_available_models() -> list[str]:
    """List models available to the current API key/project."""
    client = get_openai_client()
    models = client.models.list()
    return [m.id for m in models.data]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List models available to the current API key",
    )
    args = parser.parse_args()

    try:
        if args.list_models:
            models = list_available_models()
            for m in models:
                print(m)
        else:
            result = test_llm_connection()
            print(result)
    except ValueError as e:
        print(f"Configuration error: {e}")
    except Exception as e:
        print(f"API error: {e}")
