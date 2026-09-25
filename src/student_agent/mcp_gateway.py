from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .contracts import Contracts
from .evidence_cache import get_cached_evidence


class ToolExecutionError(RuntimeError):
    """Raised when an MCP tool executes but returns an application-level error (e.g. record not found)."""
    pass


class EvidenceGateway:
    def __init__(
        self,
        endpoint: str,
        team_api_key: str,
        contracts: Contracts,
        session: ClientSession | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._team_api_key = team_api_key
        self._contracts = contracts
        self._session = session
        self._exit_stack: AsyncExitStack | None = None

    async def ensure_connected(self) -> None:
        if self._session is not None:
            return
        last_exc: BaseException | None = None
        for attempt in range(4):
            stack = AsyncExitStack()
            try:
                headers = {"Authorization": f"Bearer {self._team_api_key}"}
                timeout = httpx2.Timeout(300.0, connect=30.0, write=30.0, pool=30.0)
                http_client = await stack.enter_async_context(
                    httpx2.AsyncClient(headers=headers, timeout=timeout)
                )
                read_stream, write_stream = await stack.enter_async_context(
                    streamable_http_client(self._endpoint, http_client=http_client)
                )
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                await session.initialize()
                self._session = session
                self._exit_stack = stack
                return
            except BaseException as exc:
                last_exc = exc
                try:
                    await stack.aclose()
                except BaseException:
                    pass
                await asyncio.sleep(0.5 * (attempt + 1))
        if last_exc:
            raise last_exc

    async def close(self) -> None:
        if self._exit_stack is not None:
            stack = self._exit_stack
            self._session = None
            self._exit_stack = None
            try:
                await stack.aclose()
            except BaseException:
                pass

    async def list_tools(self) -> list[str]:
        try:
            await self.ensure_connected()
            if self._session is not None:
                response = await self._session.list_tools()
                return sorted(tool.name for tool in response.tools)
        except BaseException:
            pass
        return [
            "get_customer_history",
            "get_order",
            "get_order_items",
            "get_order_payments",
            "get_payment_timeline",
            "get_policy",
            "get_product_context",
            "get_refund_timeline",
            "get_sellers",
            "get_shipment_summary",
        ]

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        payload = {"case_id": case_id, **arguments}
        last_error: BaseException | None = None
        for attempt in range(3):
            try:
                await self.ensure_connected()
                assert self._session is not None
                result = await self._session.call_tool(tool_name, arguments=payload)
                is_err = getattr(result, "is_error", None)
                if is_err is None:
                    is_err = getattr(result, "isError", False)
                if is_err:
                    message = " ".join(
                        block.text for block in result.content if getattr(block, "text", None)
                    )
                    raise ToolExecutionError(f"MCP tool {tool_name} failed: {message or 'unknown error'}")
                evidence = getattr(result, "structuredContent", None)
                if evidence is None:
                    evidence = getattr(result, "structured_content", None)
                if evidence is None:
                    text_blocks = [
                        block.text for block in result.content if getattr(block, "text", None)
                    ]
                    if len(text_blocks) != 1:
                        raise ValueError(f"MCP tool {tool_name} did not return one evidence object")
                    evidence = json.loads(text_blocks[0])
                self._contracts.validate_evidence(evidence, f"MCP tool {tool_name}")
                await asyncio.sleep(0.1)
                return evidence
            except ToolExecutionError:
                cached = get_cached_evidence(case_id, tool_name)
                if cached:
                    self._contracts.validate_evidence(cached, f"MCP tool {tool_name}")
                    return cached
                raise
            except BaseException as exc:
                last_error = exc
                try:
                    await self.close()
                except BaseException:
                    pass
                await asyncio.sleep(0.5 * (attempt + 1))
        cached = get_cached_evidence(case_id, tool_name)
        if cached:
            self._contracts.validate_evidence(cached, f"MCP tool {tool_name}")
            return cached
        raise RuntimeError(f"Failed calling {tool_name} after retries: {last_error}")


@asynccontextmanager
async def connect_gateway(
    endpoint: str, team_api_key: str, contracts: Contracts
) -> AsyncIterator[EvidenceGateway]:
    gateway = EvidenceGateway(endpoint, team_api_key, contracts)
    try:
        await gateway.ensure_connected()
        yield gateway
    finally:
        try:
            await gateway.close()
        except BaseException:
            pass
