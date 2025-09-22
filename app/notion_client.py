from typing import Callable, Any
from notion_client import Client
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from notion_client.errors import APIResponseError

def build_client(token: str, timeout_sec: int) -> Client:
    # notion_client 2.x: timeout_ms (snake_case) 사용
    return Client(auth=token, timeout_ms=timeout_sec * 1000)

def notion_retry():
    return retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        retry=retry_if_exception_type(APIResponseError),
    )