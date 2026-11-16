"""Real Chromium/DOM requests against the unmodified console modules (synthetic API)."""

import threading
from pathlib import Path

import pytest

from tests.support.knowledge_review_browser import (
    CONTENT,
    EMPTY_ID,
    READY_ID,
    browser_path,
    review_browser,
    review_server,
)


@pytest.fixture
def chromium_executable():
    executable = browser_path()
    # Only executable availability can abstain; launch, CDP and UI failures propagate.
    # An explicitly configured invalid path raises in browser_path(), never skips.
    if executable is None or not Path(executable).exists():
        pytest.skip("Chrome/Chromium unavailable; set LANTAI_TEST_CHROMIUM for browser smoke")
    return executable


async def open_item(page, item_id=READY_ID):
    await page.click(f'.work-row[data-id="{item_id}"]')
    await page.wait("document.querySelector('#inspectorBody .raw-details')")


async def open_review(page, decision="approve"):
    await page.click(f'#inspectorActions [data-review-decision="{decision}"]')
    await page.wait("document.querySelector('#actionDialog').open")


async def approve(page, reason="逐项核实证据", decision="approve"):
    await open_review(page, decision)
    await page.reason(reason)
    await page.click("#dialogConfirm")
    await page.wait("!document.querySelector('#actionDialog').open")


async def settle(page):
    await page.js("new Promise(resolve => setTimeout(resolve, 150))")


async def test_knowledge_candidate_list_evidence_and_batch(tmp_path, chromium_executable):
    with review_server() as (url, scenario):
        async with review_browser(tmp_path, url, chromium_executable) as page:
            assert (
                await page.js(
                    "document.querySelector('#kindFilter option[value=knowledge_candidate]')?.textContent"
                )
                == "知识候选"
            )
            await page.js(
                "document.querySelector('#kindFilter').value = 'knowledge_candidate'; document.querySelector('#kindFilter').dispatchEvent(new Event('change'))"
            )
            await page.wait("document.querySelectorAll('.work-row').length === 2")
            assert any("kind=knowledge_candidate" in path for _, path, _ in scenario.requests)
            await page.click(f'.work-row[data-id="{READY_ID}"] .row-check')
            assert await page.js("document.querySelector('#batchActions').textContent") == ""
            await page.click("#clearSelection")
            await open_item(page)
            assert (
                await page.js("document.querySelector('#inspectorKind').textContent") == "知识候选"
            )
            body = await page.js("document.querySelector('#inspectorBody').textContent")
            assert CONTENT in body
            assert "source_1" in body and "0.7" in body
            assert "evidence reliability below threshold" in body
            assert not await page.js(
                "document.querySelector('#inspectorBody img, #inspectorBody script') || window.injected"
            )
            assert await page.js(
                "[...document.querySelectorAll('[data-evidence-id]:checked')].map(x => x.value)"
            ) == ["ev_1", "ev_2"]
            assert await page.js("document.querySelector('[data-evidence-id=ev_3]').disabled")
            await page.click('[data-evidence-id="ev_1"]')
            assert await page.js(
                "document.querySelector('[data-review-decision=approve]').disabled"
            )
            assert "至少 2" in await page.js(
                "document.querySelector('#knowledgeEvidenceStatus').textContent"
            )
            await page.click('[data-evidence-id="ev_2"]')
            assert await page.js(
                "document.querySelector('[data-review-decision=approve]').disabled"
            )
            await page.click('[data-review-decision="approve"]')
            assert not await page.js("document.querySelector('#actionDialog').open")
            # Mixed selection must not smuggle knowledge candidates into batch reject.
            await page.click("#closeInspector")
            await page.js(
                "document.querySelector('#kindFilter').value = ''; document.querySelector('#kindFilter').dispatchEvent(new Event('change'))"
            )
            await page.wait("document.querySelectorAll('.work-row').length === 3")
            await page.click(f'.work-row[data-id="{READY_ID}"] .row-check')
            await page.click('.work-row[data-id="candidate:intake"] .row-check')
            assert await page.js("document.querySelector('#batchActions').textContent") == ""
            await page.click("#clearSelection")
            await open_item(page, EMPTY_ID)
            assert await page.js(
                "document.querySelector('[data-review-decision=approve]').disabled"
            )
            assert "select stored evidence" in await page.js(
                "document.querySelector('#inspectorBody').textContent"
            )
            assert scenario.posts == []
            assert scenario.unexpected == []


async def test_evidence_subset_can_pass_after_recommended_set_fails(tmp_path, chromium_executable):
    with review_server() as (url, scenario):
        scenario.low_quality_recommended = True
        async with review_browser(tmp_path, url, chromium_executable) as page:
            await open_item(page)
            assert await page.js(
                "document.querySelector('[data-review-decision=approve]').disabled"
            )
            assert "evidence quality below threshold" in await page.js(
                "document.querySelector('#inspectorBody').textContent"
            )
            await page.click('[data-evidence-id="ev_3"]')
            assert not await page.js(
                "document.querySelector('[data-review-decision=approve]').disabled"
            )
            await approve(page, "排除低独立性证据后逐项核对")
            await page.wait(
                "document.querySelector('#knowledgeReviewReceipts').textContent.includes('mem_knowledge')"
            )
            assert scenario.posts[0][2]["evidence_ids"] == ["ev_1", "ev_2"]
            assert len(scenario.posts) == 1
            assert scenario.unexpected == []


async def test_review_dialog_cancel_escape_and_single_submission(tmp_path, chromium_executable):
    with review_server() as (url, scenario):
        async with review_browser(tmp_path, url, chromium_executable) as page:
            await open_item(page)
            await open_review(page)
            assert (
                await page.js(
                    "document.querySelector('#actionDialog').getAttribute('aria-labelledby')"
                )
                == "dialogTitle"
            )
            assert await page.js("document.activeElement.tagName") == "TEXTAREA"
            ax = await page.command("Accessibility.getFullAXTree")
            assert any(
                n.get("role", {}).get("value") == "dialog"
                and n.get("name", {}).get("value") == "批准知识候选"
                for n in ax["nodes"]
            )
            await page.click("#dialogConfirm")
            assert await page.js("document.querySelector('#actionDialog').open")
            await page.reason("   ")
            await page.click("#dialogConfirm")
            assert await page.js("document.querySelector('#actionDialog').open")
            # Native Cancel bypasses required-field validation.
            await page.click('#actionDialog footer [value="cancel"]')
            await page.wait("!document.querySelector('#actionDialog').open")
            assert scenario.posts == []
            await open_review(page, "reject")
            await page.reason("取消这次拒绝")
            await page.key("Escape", "Escape", 27)
            await page.wait("!document.querySelector('#actionDialog').open")
            assert await page.js(
                "document.querySelector('.app-shell').classList.contains('inspector-open')"
            )
            assert await page.js("document.activeElement.dataset.reviewDecision") == "reject"
            assert scenario.posts == []
            # Force stale returnValue from a previous confirmation: Escape must clear it.
            await page.js("document.querySelector('#actionDialog').returnValue = 'confirm'")
            await open_review(page)
            await page.reason("不能提交这个理由")
            await page.key("Escape", "Escape", 27)
            await settle(page)
            assert scenario.posts == []
            assert await page.js("document.querySelector('#actionDialog').returnValue") == ""
            await page.key("Escape", "Escape", 27)
            assert not await page.js(
                "document.querySelector('.app-shell').classList.contains('inspector-open')"
            )
            await open_item(page)
            scenario.hold = threading.Event()
            await approve(page)
            await page.wait("document.querySelector('[data-review-retry]')?.disabled")
            # Repeated clicks while the POST is in-flight cannot enqueue another review.
            await page.click('[data-review-decision="approve"]')
            await page.click("[data-review-retry]")
            await settle(page)
            assert len(scenario.posts) == 1
            scenario.hold.set()
            await page.wait(
                "document.querySelector('#knowledgeReviewReceipts').textContent.includes('mem_knowledge')"
            )
            payload = scenario.posts[0][2]
            assert payload["reason"] == "逐项核实证据"
            assert payload["expected_version"] == 3
            assert payload["evidence_ids"] == ["ev_1", "ev_2"]
            assert 8 <= len(payload["request_id"]) <= 128
            assert not await page.js("typeof crypto.randomUUID === 'function'")
            await page.wait(
                "!document.querySelector('.work-row[data-id=\"knowledge_candidate:mem_ready\"]')"
            )
            await open_item(page, EMPTY_ID)
            await open_review(page, "reject")
            await page.reason("成功审核后再取消")
            await page.key("Escape", "Escape", 27)
            await page.wait("!document.querySelector('#actionDialog').open")
            assert len(scenario.posts) == 1
            assert await page.js(
                "document.querySelector('.app-shell').classList.contains('inspector-open')"
            )
            assert scenario.unexpected == []


@pytest.mark.parametrize("outcome", ["disconnect", "503"])
async def test_uncertain_retry_retains_exact_body_and_receipt(
    tmp_path, chromium_executable, outcome
):
    with review_server() as (url, scenario):
        scenario.outcomes = [outcome, "success"]
        async with review_browser(tmp_path, url, chromium_executable) as page:
            await open_item(page)
            await approve(page, reason="同一次审核，请保留收据")
            await page.wait(
                "document.querySelector('[data-review-retry]') && !document.querySelector('[data-review-retry]').disabled"
            )
            assert len(scenario.posts) == 1
            first = scenario.posts[0][2]
            # The original candidate can disappear after a lost response. Recovery
            # survives closing details, queue refresh and a real page reload.
            await page.click("#closeInspector")
            await page.reload()
            await page.wait(
                "document.querySelector('[data-review-retry]') && !document.querySelector('[data-review-retry]').disabled"
            )
            await page.click("[data-review-retry]")
            await page.wait(
                "document.querySelector('#knowledgeReviewReceipts').textContent.includes('mem_knowledge')"
            )
            assert len(scenario.posts) == 2
            assert scenario.posts[1][2] == first
            receipt_text = await page.js(
                "document.querySelector('#knowledgeReviewReceipts').textContent"
            )
            assert first["request_id"] in receipt_text and "ckpt_test" in receipt_text
            await page.click("#refreshButton")
            await settle(page)
            assert first["request_id"] in await page.js(
                "document.querySelector('#knowledgeReviewReceipts').textContent"
            )
            assert scenario.unexpected == []


@pytest.mark.parametrize("outcome", ["409", "422"])
async def test_stale_or_ineligible_review_refreshes_without_auto_retry(
    tmp_path, chromium_executable, outcome
):
    with review_server() as (url, scenario):
        scenario.outcomes = [outcome]
        async with review_browser(tmp_path, url, chromium_executable) as page:
            await open_item(page)
            before = sum(path.endswith("/mem_ready") for _, path, _ in scenario.requests)
            await approve(page)
            await page.wait(
                "!document.querySelector('[data-review-retry]') && document.querySelector('#knowledgeReviewReceipts').textContent.includes('未通过')"
            )
            await settle(page)
            assert len(scenario.posts) == 1
            assert sum(path.endswith("/mem_ready") for _, path, _ in scenario.requests) > before
            if outcome == "409":
                await approve(page, "刷新后重新核对")
                await page.wait(
                    "document.querySelector('#knowledgeReviewReceipts').textContent.includes('mem_knowledge')"
                )
                assert scenario.posts[1][2]["expected_version"] == 4
                assert scenario.posts[1][2]["request_id"] != scenario.posts[0][2]["request_id"]
            else:
                assert await page.js(
                    "document.querySelector('[data-review-decision=approve]').disabled"
                )
                assert "candidate confidence below threshold" in await page.js(
                    "document.querySelector('#knowledgeReviewReceipts').textContent"
                )
            assert scenario.unexpected == []


async def test_reject_without_evidence_and_320px_layout(tmp_path, chromium_executable):
    with review_server() as (url, scenario):
        async with review_browser(tmp_path, url, chromium_executable, width=320) as page:

            async def no_overflow():
                dimensions = await page.js(
                    "({page: document.documentElement.scrollWidth, viewport: innerWidth})"
                )
                assert dimensions["page"] <= dimensions["viewport"], dimensions

            await no_overflow()
            await open_item(page)
            await no_overflow()
            await page.click("#closeInspector")
            await open_item(page, EMPTY_ID)
            await no_overflow()
            await open_review(page, "reject")
            await no_overflow()
            await page.reason("缺少可追溯证据，归档待补")
            await page.click("#dialogConfirm")
            await page.wait(
                "document.querySelector('#knowledgeReviewReceipts').textContent.includes('archived')"
            )
            assert len(scenario.posts) == 1
            assert scenario.posts[0][2]["decision"] == "reject"
            assert scenario.posts[0][2]["evidence_ids"] == []
            await no_overflow()
            assert scenario.unexpected == []


def test_review_assets_have_native_named_dialog_and_no_html_injection():
    ui = Path(__file__).resolve().parents[1] / "lantai" / "api" / "ui"
    html = (ui / "index.html").read_text(encoding="utf-8")
    app = (ui / "app.js").read_text(encoding="utf-8")
    assert '<option value="knowledge_candidate">知识候选</option>' in html
    assert '<dialog id="actionDialog" aria-labelledby="dialogTitle">' in html
    assert "innerHTML" not in app
