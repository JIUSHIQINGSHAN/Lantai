"""Real product scripts: keyboard, drawer, layout and motion preference regressions."""

from pathlib import Path

import pytest

from tests.support.ui_browser import chromium, product_server


@pytest.mark.asyncio
async def test_studio_tabs_keyboard_matches_selected_panel(tmp_path: Path) -> None:
    with product_server() as (url, _requests, unexpected):
        async with chromium(tmp_path, url) as browser:
            await browser.evaluate(
                "document.querySelector('.primary-nav [data-view=studio]').click()"
            )
            await browser.wait("document.querySelector('#studioView').classList.contains('active')")
            await browser.evaluate("document.querySelector('[data-studio-tab=persona]').focus()")
            for key, code, number, expected in [
                ("ArrowRight", "ArrowRight", 39, "scratchpad"),
                ("Home", "Home", 36, "persona"),
                ("End", "End", 35, "prompts"),
                ("ArrowLeft", "ArrowLeft", 37, "recall"),
            ]:
                await browser.key(key, code, number)
                assert (
                    await browser.evaluate("document.activeElement.dataset.studioTab") == expected
                )
                assert (
                    await browser.evaluate(
                        "document.querySelector('[role=tab][aria-selected=true]').dataset.studioTab"
                    )
                    == expected
                )
                assert (
                    await browser.evaluate(
                        "[...document.querySelectorAll('.studio-tab-panel')].filter(p => !p.hidden).length"
                    )
                    == 1
                )
                assert (
                    await browser.evaluate(
                        "[...document.querySelectorAll('[role=tab]')].filter(p => p.tabIndex === 0).length"
                    )
                    == 1
                )
            await browser.wait(
                "!document.querySelector('#rrRaw').textContent.includes('读取召回报告异常')"
            )
        assert unexpected == []


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [320, 1440])
async def test_drawer_long_text_and_motion_preference(tmp_path: Path, width: int) -> None:
    with product_server() as (url, _requests, unexpected):
        async with chromium(tmp_path, url, width) as browser:
            await browser.evaluate(
                "document.querySelector('.work-row').focus(); document.querySelector('.work-row').click()"
            )
            await browser.wait("document.querySelector('#inspectorActions button')")
            await browser.evaluate(
                "document.querySelector('#inspectorTitle').textContent = '很长的案牍标题'.repeat(30)"
            )
            assert await browser.evaluate("document.documentElement.scrollWidth <= innerWidth")
            scales = await browser.evaluate("""(() => {
              const title = document.querySelector('#inspectorTitle');
              const body = document.querySelector('#inspectorBody p');
              const before = [title, body].map(el => parseFloat(getComputedStyle(el).fontSize));
              [title, body].forEach((el, i) => { el.style.fontSize = (before[i] * 2) + 'px'; });
              return before;
            })()""")
            await browser.wait(
                f"parseFloat(getComputedStyle(document.querySelector('#inspectorTitle')).fontSize) === {scales[0] * 2}"
                f" && parseFloat(getComputedStyle(document.querySelector('#inspectorBody p')).fontSize) === {scales[1] * 2}"
            )
            enlarged = await browser.evaluate("""(() => ({
              title: parseFloat(getComputedStyle(document.querySelector('#inspectorTitle')).fontSize),
              body: parseFloat(getComputedStyle(document.querySelector('#inspectorBody p')).fontSize),
              viewport: innerWidth,
              scroll: document.documentElement.scrollWidth,
              close: (() => {const r=document.querySelector('#closeInspector').getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2,width:r.width,height:r.height}})()
            }))()""")
            assert enlarged["title"] == scales[0] * 2
            assert enlarged["body"] == scales[1] * 2
            assert enlarged["scroll"] <= enlarged["viewport"], enlarged
            hit = await browser.evaluate(
                f"""(() => {{
                  const button = document.querySelector('#closeInspector');
                  const target = document.elementFromPoint({enlarged["close"]["x"]}, {enlarged["close"]["y"]});
                  return target === button || button.contains(target);
                }})()"""
            )
            assert hit, enlarged
            event = {"button": "left", "clickCount": 1, **enlarged["close"]}
            await browser.send("Input.dispatchMouseEvent", {"type": "mousePressed", **event})
            await browser.send("Input.dispatchMouseEvent", {"type": "mouseReleased", **event})
            assert await browser.evaluate("document.activeElement.classList.contains('work-row')")
            await browser.send(
                "Emulation.setEmulatedMedia",
                {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]},
            )
            assert await browser.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
            assert await browser.evaluate(
                "parseFloat(getComputedStyle(document.querySelector('.work-row')).transitionDuration) < 0.01"
            )
        assert unexpected == []
