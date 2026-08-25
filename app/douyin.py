from __future__ import annotations

import asyncio

from playwright.async_api import Locator, Page

from app.selectors import CHAT_PANEL_MARKERS, MESSAGE_INPUTS, SEARCH_INPUTS


class PageOperationError(RuntimeError):
    pass


RETRY_DELAY_MS = 3_000


class DouyinChat:
    def __init__(
        self,
        page: Page,
        timeout_ms: int = 15_000,
        confirm_timeout_ms: int = 15_000,
    ) -> None:
        self.page = page
        self.timeout_ms = timeout_ms
        self.confirm_timeout_ms = confirm_timeout_ms

    async def open_target(self, name: str, retries: int = 1) -> None:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                await self._open_target_once(name)
                return
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    await self.page.wait_for_timeout(RETRY_DELAY_MS)
        if last_error is not None:
            raise last_error
        raise PageOperationError("打开聊天失败")

    async def _open_target_once(self, name: str) -> None:
        search = await first_visible(self.page, SEARCH_INPUTS, self.timeout_ms)
        await search.click()
        await search.fill("")
        await search.fill(name)
        await self.page.wait_for_timeout(1_500)

        result = await self._search_result(name)
        if result is None:
            raise PageOperationError("搜索不到目标好友")
        await result.click(force=True)
        await self._confirm_opened(name)

    async def _search_result(self, name: str) -> Locator | None:
        # Search mode renders a separate SearchPanel. Its "发消息" action is the
        # correct control; clicking the hidden conversation cache does not mount
        # the composer.
        #
        # Identity must be resolved from the per-result name node, never from the
        # collection container: `[class*="SearchPanelitem"]` also matches an outer
        # `SearchPanelitems` wrapper, whose descendants would then contain the
        # target name while `.first` returns another row's button. Scope to result
        # rows and require the matched name node and its button to be visible here.
        search_items = self.page.locator('[class*="SearchPanelitembox"], [class*="SearchPanelitem-box"], [class*="SearchPanelitem_box"]')
        name_selectors = (
            '[class*="SearchPanelitemtitle"]',
            '[class*="SearchPanelitemTitle"]',
            '[class*="SearchPanelitem_title"]',
            '[class*="SearchPanelitem-title"]',
            '[class*="SearchPanelitemname"]',
            '[class*="SearchPanelitemName"]',
            '[class*="SearchPanelitem_name"]',
            '[class*="SearchPanelitem-name"]',
        )
        for index in range(await search_items.count()):
            item = search_items.nth(index)
            name_locator = await _visible_exact_text_locator(item, name_selectors, name)
            if name_locator is None:
                continue
            button = item.locator('[class*="SearchPanelitemchat_btn"]').first
            try:
                if await button.count() and await button.is_visible():
                    return button
            except Exception:
                continue

        # The nickname node can be hidden while its conversation row is visible.
        # Locate and click the complete row instead of relying on text visibility.
        row_selectors = (
            '[data-e2e="conversation-item"]',
            '[class*="conversationConversationItem"]',
            '[class*="conversation-item"]',
            '[class*="ConversationItem"]',
        )
        title_selectors = (
            '[class*="conversationConversationItemtitle"]',
            '[class*="ConversationItemtitle"]',
            '[class*="ConversationItemTitle"]',
            '[class*="conversation-item-title"]',
            '[class*="conversation-item-Title"]',
        )
        for selector in row_selectors:
            rows = self.page.locator(selector)
            for index in range(await rows.count()):
                row = rows.nth(index)
                title_locator = await _visible_exact_text_locator(row, title_selectors, name)
                if title_locator is None:
                    continue
                try:
                    if await row.is_visible():
                        return row
                except Exception:
                    continue

        # Some Douyin builds render the title itself as hidden, but keep a visible
        # ancestor as the actionable result. Find that ancestor from the hidden title.
        hidden_titles = self.page.locator('[class*="conversationConversationItemtitle"]')
        for index in range(await hidden_titles.count()):
            title = hidden_titles.nth(index)
            if not await _text_equals(title, name):
                continue
            row = title.locator(
                "xpath=ancestor::*[contains(@class, 'conversationConversationItem')][1]"
            )
            if await row.count() and await row.is_visible():
                return row

        return None

    async def message_input(self) -> Locator:
        return await first_visible(self.page, MESSAGE_INPUTS, self.timeout_ms)

    async def _confirm_opened(self, name: str, timeout_ms: int | None = None) -> None:
        timeout = timeout_ms if timeout_ms is not None else self.confirm_timeout_ms
        deadline = asyncio.get_running_loop().time() + timeout / 1000
        while True:
            last_error = await self._chat_open_error(name)
            if last_error is None:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise last_error
            await self.page.wait_for_timeout(500)

    async def _chat_open_error(self, name: str) -> PageOperationError | None:
        # Confirm the right-side current chat by the authoritative chat title, which
        # must itself be visible. A visible header retaining a hidden stale name node
        # (common during SPA transitions) must not confirm the wrong recipient, and
        # a secondary username/title field must never substitute for the chat title.
        title_selectors = (
            '[class*="RightPanelHeadertitle"]',
            '[class*="RightPanelHeaderTitle"]',
            '[class*="RightPanelHeader_title"]',
            '[class*="RightPanelHeader-title"]',
            '[class*="chatHeadertitle"]',
            '[class*="ChatHeaderTitle"]',
            '[class*="chatHeader_title"]',
            '[class*="ChatHeader-title"]',
            '[class*="name"]',
            '[class*="Name"]',
            '[class*="nickname"]',
            '[class*="Nickname"]',
        )
        for selector in CHAT_PANEL_MARKERS[:3]:
            headers = self.page.locator(selector)
            for index in range(await headers.count()):
                header = headers.nth(index)
                try:
                    if not await header.is_visible():
                        continue
                except Exception:
                    continue
                if await _visible_exact_text_in(header, title_selectors, name):
                    return None

        composer_visible = await self._composer_visible()
        return PageOperationError(
            f"点击搜索结果后无法确认聊天已打开（输入框: {'有' if composer_visible else '无'}）"
        )

    async def _composer_visible(self) -> bool:
        for selector in MESSAGE_INPUTS:
            locator = self.page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    return True
            except Exception:
                continue
        return False


async def _visible_exact_text_in(container: Locator, selectors: tuple[str, ...], expected: str) -> bool:
    return await _visible_exact_text_locator(container, selectors, expected) is not None


async def _visible_exact_text_locator(
    container: Locator, selectors: tuple[str, ...], expected: str
) -> Locator | None:
    for selector in selectors:
        nodes = container.locator(selector)
        for index in range(await nodes.count()):
            node = nodes.nth(index)
            if await _text_equals(node, expected):
                try:
                    if await node.is_visible():
                        return node
                except Exception:
                    continue
    return None


async def _has_exact_text_in(container: Locator, selectors: tuple[str, ...], expected: str) -> bool:
    for selector in selectors:
        if await _has_exact_text(container.locator(selector), expected):
            return True
    return False


async def _has_exact_text(locators: Locator, expected: str) -> bool:
    for index in range(await locators.count()):
        if await _text_equals(locators.nth(index), expected):
            return True
    return False


async def _text_equals(locator: Locator, expected: str) -> bool:
    try:
        return (await locator.inner_text(timeout=500)).strip() == expected
    except Exception:
        return False


async def first_visible(page: Page, selectors: tuple[str, ...], timeout_ms: int = 15_000) -> Locator:
    per_selector = max(500, timeout_ms // max(1, len(selectors)))
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=per_selector)
            return locator
        except Exception:
            continue
    raise PageOperationError(f"找不到页面元素，已尝试: {', '.join(selectors)}")
