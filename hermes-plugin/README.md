# Hermes 入口

`lantai-hook/` 是独立 Python 插件，经宿主 `register_hook` 登记
`pre_llm_call` / `on_session_end`，沿用现有 NDJSON shell hook 协议。
不导入 Hermes 或兰台业务包。安装方式仍见
[已有部署说明](../docs/lantai-hermes-plugin.md)，本次开发不会安装或重启宿主。

## 配置校验

- 可显式设置 `LANTAI_PYTHON` 与 `LANTAI_SHELL_HOOK`，值必须为存在的绝对文件路径。
- 未显式设置时保留既有探测顺序；显式设置错误、空值或相对路径时禁用入口，
  日志提示配置无效，不静默回退到另一份代码或数据。
- 启动子进程前再次校验路径。子进程固定 `LANTAI_HOST=hermes`，清除
  `PYTHONPATH` / `PYTHONHOME` 并使用 UTF-8，不改父进程环境。
- 超时配置非数字、非正数或非有限值时沿用既有默认墙宽，不产生无限等待。

## 数据与回执

字符串消息照常处理；多模态内容列表只拼接 `type=text` 的文字段，忽略图片等内容。
非法会话标识不会查询或写入。保留 cron / subagent 只读隔离、会话首轮底本、
正常终态延后回执与被否决轮作废协议，未修改核心存储与服务契约。

新增公共入口与真实子进程测试位于
`tests/test_adapter_hermes_openclaw.py`，既有插件/协议测试继续独立验收。
