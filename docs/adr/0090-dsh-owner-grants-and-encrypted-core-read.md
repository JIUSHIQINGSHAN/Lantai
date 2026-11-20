# ADR-0090：DSH 会话授权与加密核心记忆读取

- 状态：已实现并通过隔离验收，日常接入已应用；待用户界面许可后的生产读取验证
- 日期：2026-11-20
- 范围：独立DSH核心记忆bundle；不修改DSH内核、远程兰台数据或旧MCP连接

## 背景

实际接入检查发现两个不能靠工作目录绑定代替的前提：现有云端桥使用公网HTTP，而当前DSH 0.2.2-rc.3的agent/created不携带可信用户权限。SessionHeader没有owner/grants；RemoteAccess.allows在Remote之外默认允许，principalOf又把owner与缺上下文都折叠为undefined，不能用这些缺省值签发读取许可。

已有受信OpenSSH连接可验证主机身份，并已通过-W访问服务器loopback上的无凭据health检查。无需更改云服务器或新建公网TLS服务即可获得端到端加密读取路径，但传输安全不等于会话授权。

## 决策

1. 引入SessionGrant（描述性技术术语，见CONTEXT词汇表）。只允许实际用户控制面的Remote方法，在Host固定的invocation.peer.principal明确为owner、Peer和请求仍有效时，为精确live Agent及资料scope签发。绑定包含工作目录、namespace、userId、tenantId、是否读取task及连接身份；不只绑定可复用session字符串。重启、Agent替换、scope变化、撤销、过期或异常均不复活旧许可。
2. 模型只有状态和已授权后的刷新能力，不提供签发/确认许可的工具。用户问题服务可被本地answerer回答，命令handler没有Peer；两者的答案标签、source.kind都不充当权限证明。客户端只负责用户操作，Host再次独立检查真实调用者。
3. 凭据解析、旧凭据源读取、SSH启动和核心读取均发生在授权及scope校验之后。读取中失效须取消并清空当前快照；无授权时无fetch、无记忆data帧。既有历史不自动删除。
4. 保留现有HTTPS读取Adapter，新增SSH stdio Adapter，共用小Interface：read(binding, token, signal)。SSH固定预期host/user/port、严格known-host与BatchMode，-W只指向服务器loopback端口；不开放本地监听口、不启动远端shell、不转发agent/X11/任意环境、不留下常驻隧道。凭据仅在加密SSH stdin中的HTTP头出现，不在argv/env或日志。
5. 现有旧连接的JSON只作为显式operator配置的可选凭据源，且仅owner控制面在scope明确后读取；优先复用已有DSH凭据引用。缺少该引用时只在当前许可的会话内存暂存，撤销/到期/释放时清除，不调用无法取消的凭据持久化写入，不写源码/profile明文，不把密钥或core正文回传Client。旧桥与旧数据不自动更改，NULL归属不自动认领。
6. 全部读取仍严格执行CoreMemoryBlock身份、版本、唯一性、UTF-8及字节预算；SSH失败不得回退到公网HTTP。用户许可不等于放宽server ACL或模型系统指令。

## 取舍

- 对比改造生产HTTPS服务：复用已验证SSH不需要中断现有云端服务；成本是每次读取的握手和进程生命周期管理。
- 对比本地端口隧道：stdio避免长期监听口、端口抢占和悬挂后台任务；成本是受控HTTP流与退出清理实现。
- 对比“所有同目录会话自动读取”：精确许可消除不可信或不明调用者的数据读取。未经许可的首请求保持无资料；仅在用户授权后加载，不能谎称未授权首请求已经有记忆。若空白会话尚无live Agent，客户端应如实提示先建立会话，不复制原生Agent创建/预设流程。

## 验证与限制

- 必须实测真实Gateway owner/nonowner/无invocation/取消Peer与wire伪造；不能以配置布尔、工作目录或用户文字“已同意”代替。
- 模型刷新在无许可时应0credential/0SSH/0HTTP/0data；有效后仍按当前scope约束，撤销/过期/工作目录往返和双owner释放不迟到。
- SSH Adapter用真实受控子进程验证流、大小、错误、编码和取消；现场仅先做无凭据health，不能据此声称生产core归属已核对。
- UI无浏览器控制时，源码/注册/Host测试不等于视觉验收；实际用户许可需要真正owner界面动作。
