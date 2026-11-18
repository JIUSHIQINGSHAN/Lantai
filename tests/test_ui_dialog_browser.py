"""Product dialogs: real accessibility tree, keyboard and observable request boundary."""

from pathlib import Path

import pytest

from tests.support.ui_browser import chromium, product_server


@pytest.mark.asyncio
async def test_connection_dialog_name_focus_and_cancel(tmp_path: Path) -> None:
    with product_server() as (url, requests, unexpected):
        async with chromium(tmp_path, url) as browser:
            await browser.evaluate(
                "document.querySelector('#connectionButton').focus(); document.querySelector('#connectionButton').click()"
            )
            await browser.wait("document.querySelector('#connectionDialog').open")
            tree = await browser.send("Accessibility.getFullAXTree")
            dialogs = [
                row
                for row in tree["nodes"]
                if row.get("role", {}).get("value") == "dialog" and not row.get("ignored")
            ]
            assert [row["name"]["value"] for row in dialogs] == ["连接设置"]
            assert await browser.evaluate("document.activeElement.id") == "apiKeyInput"
            for modifiers in (0, 8):
                await browser.evaluate("document.querySelector('#apiKeyInput').focus()")
                await browser.key("Tab", "Tab", 9, modifiers=modifiers)
                assert await browser.evaluate(
                    "document.querySelector('#connectionDialog').contains(document.activeElement)"
                ), await browser.evaluate(
                    "({tag: document.activeElement.tagName, id: document.activeElement.id, focused: document.hasFocus()})"
                )
            before = len(requests)
            await browser.key("Escape", "Escape", 27)
            await browser.wait("!document.querySelector('#connectionDialog').open")
            assert await browser.evaluate("document.activeElement.id") == "connectionButton"
            assert (
                await browser.evaluate("document.querySelector('#connectionDialog').returnValue")
                == ""
            )
            assert len(requests) == before
            assert await browser.evaluate("sessionStorage.length + localStorage.length") == 0
        assert unexpected == []


@pytest.mark.asyncio
async def test_approval_dialog_name_and_repeat_cancel_do_not_approve(tmp_path: Path) -> None:
    with product_server() as (url, requests, unexpected):
        async with chromium(tmp_path, url) as browser:
            await browser.evaluate("document.querySelector('.work-row').click()")
            await browser.wait("document.querySelector('#inspectorActions button')")
            await browser.evaluate(
                "[...document.querySelectorAll('#inspectorActions button')].find(b => b.textContent === '批准').click()"
            )
            await browser.wait("document.querySelector('#actionDialog').open")
            tree = await browser.send("Accessibility.getFullAXTree")
            names = [
                row.get("name", {}).get("value")
                for row in tree["nodes"]
                if row.get("role", {}).get("value") == "dialog" and not row.get("ignored")
            ]
            assert names == ["批准提案"]
            assert await browser.evaluate("document.activeElement.textContent") == "取消"
            await browser.key("Escape", "Escape", 27)
            await browser.wait("!document.querySelector('#actionDialog').open")
            assert not [row for row in requests if row[0] == "POST"]
            for cancel_selector in ("footer [value=cancel]", "header [value=cancel]"):
                await browser.evaluate(
                    "[...document.querySelectorAll('#inspectorActions button')].find(b => b.textContent === '批准').click()"
                )
                await browser.wait("document.querySelector('#actionDialog').open")
                await browser.evaluate(
                    f"document.querySelector('#actionDialog {cancel_selector}').click()"
                )
                await browser.wait(
                    "!document.querySelector('#actionDialog').open && document.querySelector('#actionDialog').returnValue === ''"
                )
                assert not [row for row in requests if row[0] == "POST"]
            await browser.evaluate(
                "[...document.querySelectorAll('#inspectorActions button')].find(b => b.textContent === '批准').click()"
            )
            await browser.wait("document.querySelector('#actionDialog').open")
            await browser.evaluate("document.querySelector('#dialogConfirm').click()")
            await browser.wait(
                "!document.querySelector('#actionDialog').open && document.querySelector('#actionDialog').returnValue === ''"
            )
            await browser.wait("document.querySelector('#toast').textContent === '提案已应用'")
            assert len([row for row in requests if row[0] == "POST"]) == 1
            assert [row[2] for row in requests if row[0] == "POST"] == [
                {"approve": True, "reason": "console approved"}
            ]
            await browser.wait("document.querySelector('.work-row')")
            await browser.evaluate("document.querySelector('.work-row').click()")
            await browser.wait("document.querySelector('#inspectorActions button')")
            await browser.evaluate(
                "[...document.querySelectorAll('#inspectorActions button')].find(b => b.textContent === '批准').click()"
            )
            await browser.wait("document.querySelector('#actionDialog').open")
            await browser.key("Escape", "Escape", 27)
            await browser.wait("!document.querySelector('#actionDialog').open")
            assert len([row for row in requests if row[0] == "POST"]) == 1
        assert unexpected == []


@pytest.mark.asyncio
async def test_required_reason_blocks_confirm_but_allows_cancel(tmp_path: Path) -> None:
    with product_server() as (url, requests, unexpected):
        async with chromium(tmp_path, url) as browser:
            await browser.evaluate("document.querySelector('.work-row').click()")
            await browser.wait("document.querySelector('#inspectorActions button')")
            for selector in ("footer [value=cancel]", "header [value=cancel]"):
                await browser.evaluate(
                    "[...document.querySelectorAll('#inspectorActions button')].find(b => b.textContent === '拒绝').click()"
                )
                await browser.wait("document.querySelector('#actionDialog').open")
                assert await browser.evaluate("document.activeElement.tagName") == "TEXTAREA"
                await browser.evaluate("document.querySelector('#dialogConfirm').click()")
                assert await browser.evaluate("document.querySelector('#actionDialog').open")
                assert not [r for r in requests if r[0] == "POST"]
                await browser.evaluate(
                    f"document.querySelector('#actionDialog {selector}').click()"
                )
                await browser.wait("!document.querySelector('#actionDialog').open")
                assert not [r for r in requests if r[0] == "POST"]
        assert unexpected == []


@pytest.mark.asyncio
async def test_connection_save_cancel_and_clear_use_only_current_choice(tmp_path: Path) -> None:
    with product_server() as (url, _requests, unexpected):
        async with chromium(tmp_path, url) as browser:
            await browser.evaluate(
                "document.querySelector('#connectionButton').click(); document.querySelector('#apiKeyInput').value = 'synthetic-key'; document.querySelector('#connectionForm [value=save]').click()"
            )
            await browser.wait(
                "sessionStorage.getItem('lantai-api-key-session') === 'synthetic-key'"
            )
            assert await browser.evaluate("localStorage.getItem('lantai_api_key')") is None
            await browser.evaluate(
                "document.querySelector('#connectionButton').click(); document.querySelector('#apiKeyInput').value = 'unsaved-key'"
            )
            await browser.key("Escape", "Escape", 27)
            await browser.wait("!document.querySelector('#connectionDialog').open")
            assert (
                await browser.evaluate("sessionStorage.getItem('lantai-api-key-session')")
                == "synthetic-key"
            )
            await browser.evaluate(
                "document.querySelector('#connectionButton').click(); document.querySelector('#connectionForm [value=clear]').click()"
            )
            await browser.wait("sessionStorage.getItem('lantai-api-key-session') === null")
            assert (
                await browser.evaluate("document.querySelector('#connectionDialog').returnValue")
                == ""
            )
        assert unexpected == []
