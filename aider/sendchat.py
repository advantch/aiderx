import hashlib
import json
import backoff
import openai
import requests
import os
from enum import Enum
from dataclasses import dataclass
from typing import Literal, List, Dict
from anthropic import Anthropic, HUMAN_PROMPT, AI_PROMPT, AsyncAnthropic, APIStatusError
from openai.error import (
    APIConnectionError,
    APIError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

CACHE_PATH = "~/.aider.send.cache.v1"
CACHE = None

def check_api_keys():
    openai_api_key = os.getenv("OPENAI_API_KEY")
    anthropic_api_key = os.environ["ANTHROPIC_API_KEY"]
    if anthropic_api_key:
        return anthropic_api_key
    if openai_api_key:
        return False
    else:
        return False

class OpenAIChatBot:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")

    @backoff.on_exception(
        backoff.expo,
        (
            Timeout,
            APIError,
            ServiceUnavailableError,
            RateLimitError,
            APIConnectionError,
            requests.exceptions.ConnectionError,
        ),
        max_tries=10,
        on_backoff=lambda details: print(
            f"{details.get('exception','Exception')}\nRetry in {details['wait']:.1f} seconds."
        ),
    )
    def send_with_retries(self, model, messages, functions, stream):
        kwargs = dict(
            model=model,
            messages=messages,
            temperature=0,
            stream=stream,
        )
        if functions is not None:
            kwargs["functions"] = functions

        if hasattr(openai, "api_deployment_id"):
            kwargs["deployment_id"] = openai.api_deployment_id
        if hasattr(openai, "api_engine"):
            kwargs["engine"] = openai.api_engine

        key = json.dumps(kwargs, sort_keys=True).encode()
        hash_object = hashlib.sha1(key)

        if not stream and CACHE is not None and key in CACHE:
            return hash_object, CACHE[key]

        res = openai.ChatCompletion.create(**kwargs)

        if not stream and CACHE is not None:
            CACHE[key] = res

        return hash_object, res

    def simple_send_with_retries(self, model, messages):
        try:
            _hash, response = self.send_with_retries(
                model=model,
                messages=messages,
                functions=None,
                stream=False,
            )
            return response.choices[0].message.content
        except (AttributeError, openai.error.InvalidRequestError):
            return

class AnthropicChatBot:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.anthropic = Anthropic(auth_token=api_key)
        self.anthropic_chat = AsyncAnthropic(auth_token=api_key)
        self.converter: PromptConverter = PromptConverter()

    async def create(self, prompt=None, max_tokens_to_sample=None, **kwargs):
        response = await self.async_stream(
            prompt=prompt, max_tokens_to_sample=max_tokens_to_sample, **kwargs
        )
        return response.completion.message

    def is_claude(self, model):
        return model.startswith("claude")

    def generate_prompt(self, prompt, model=None, **kwargs):
        if model and self.is_claude(model):
            try:
                prompt = self.converter.convert_to_anthropic(prompt)
                response = self.anthropic.completions.create(
                    model, max_tokens_to_sample, prompt, **kwargs
                )
                if self.model:
                    max_tokens_to_sample = 90000
                if not prompt:
                    raise ValueError("Prompt cannot be empty")
            except ValueError as e:
                return f"Error: {str(e)}"
            return response.completion

    async def async_stream(
        self,
        prompt: str,
        model: str = None,
        stream: bool = None,
        max_tokens_to_sample: int = None,
        **kwargs,
    ):
        if self.is_claude(model=model):
            stream = (True,)
            max_tokens_to_sample = 90000
            try:
                if not prompt:
                    raise ValueError("Prompt cannot be empty")
                anthropic_message = self.prompt_converter.convert_to_anthropic(
                    prompt, max_tokens_to_sample, **kwargs
                )
                stream: GeneratorExit = await self.anthropic_chat.completions.create(
                    model=model,
                    prompt=[message for message in anthropic_message],
                    maxtokens_to_sample=90000,
                    stream=True,
                    extra_headers=None,
                    **kwargs,
                )
                async for completion in stream:
                    response = completion.completion, end = ""
            except APIStatusError as err:
                print(
                    f"Caught API status error with response body: {err.response.text}"
                )
                raise Exception(err.response.text)
        return response

@dataclass
class RoleOptions(Enum):
    USER: Literal["user"]
    ASSISTANT: Literal["assistant"]

class PromptConverter:
    def __init__(self):
        self.human_prompt: Literal["Human:\n\n"] = HUMAN_PROMPT
        self.ai_prompt: Literal["Assistant:\n\n"] = AI_PROMPT
        self.role_options: RoleOptions = RoleOptions

    def convert_to_anthropic(self, message_dict: Dict[str, str]) -> str:
        prompt: str = ""
        for role, content in message_dict.items():
            if role == self.role_options.USER:
                prompt += self.human_prompt + content
            if role == self.role_options.ASSISTANT:
                prompt += self.ai_prompt + content
        return prompt

    def convert_to_openai(self, message_string: str) -> List[Dict[str, str]]:
        response = {}
        messages = message_string.split("\n\n")
        for message in messages:
            role, content = message.split(":")
            response[role.strip()] = self.generate_prompt(content.strip())
        return response

class AnthropicTokens:
    text = None

    def __init__(self, text):
        self.tokens = []
        self.client = AnthropicChatBot(api_key).anthropic
        self.text = text
        text = text

    def sync_tokens(self, text: str):
        self.tokens = self.client.count_tokens(text)
        print(f"'{text} is {self.tokens} tokens'")

    def async_tokens(
        self,
        text: str,
    ) -> str:
        self.tokens = self.client.count_tokens(text)
        print(f"'{text} is {self.tokens} tokens'")

    def run(self, text):
        self.sync_tokens(text)
        asyncio.run(self.async_tokens(text))

    asyncio.run(text)

def send_with_retries(model, messages, functions, stream):
    api_key = check_api_keys()
    if api_key:
        bot = AnthropicChatBot(api_key)
    else:
        bot = OpenAIChatBot()
    return bot.send_with_retries(model, messages, functions, stream)

def simple_send_with_retries(model, messages):
    api_key = check_api_keys()
    if api_key:
        bot = AnthropicChatBot(api_key)
    else:
        bot = OpenAIChatBot()
    return bot.simple_send_with_retries(model, messages)
