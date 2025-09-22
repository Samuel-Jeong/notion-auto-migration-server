from notion_client import Client
from notion_client.errors import APIResponseError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

def build_client(token: str, timeout: int = 15) -> Client:
    # notion-client 2.x에서는 timeout_ms 파라미터 사용
    return Client(auth=token, timeout_ms=timeout * 1000)
    
def notion_retry(max_attempts: int = 3):
    return retry(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=0.6, min=0.6, max=5),
        retry=retry_if_exception_type(APIResponseError),
    )