"""正式包标记（票 .scratch/v0.68-next-window-2026-11/04）：勿删。

命名空间遮蔽防线——宿主 PYTHONPATH 里的外来正式 `tests` 包会压过本仓的
命名空间部分（Python 规则：任何位置的正式包胜出，与 sys.path 顺序无关）；
本包升级为正式包后，pytest `pythonpath=["."]` 注入的仓库根排在首位，
正包对正包按位置定胜负，本地包稳定反压。锁：test_pytest_collection_env_shading。
"""
