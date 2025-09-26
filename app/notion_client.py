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

@notion_retry()
def get_page(client: Client, page_id: str) -> dict:
    """Retrieve page information"""
    return client.pages.retrieve(page_id=page_id)

@notion_retry()
def get_database(client: Client, database_id: str) -> dict:
    """Retrieve database information"""
    return client.databases.retrieve(database_id=database_id)

@notion_retry()
def query_database(client: Client, database_id: str, start_cursor: str = None, page_size: int = 100) -> dict:
    """Query database entries with pagination"""
    kwargs = {"database_id": database_id, "page_size": page_size}
    if start_cursor:
        kwargs["start_cursor"] = start_cursor
    return client.databases.query(**kwargs)

@notion_retry()
def create_database(client: Client, parent_page_id: str, title: str, properties: dict) -> dict:
    """Create a new database"""
    return client.databases.create(
        parent={
            "type": "page_id",
            "page_id": parent_page_id
        },
        title=[{
            "type": "text",
            "text": {"content": title}
        }],
        properties=properties
    )