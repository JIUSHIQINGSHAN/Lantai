# 为兰台记忆（Lantai）贡献

感谢您对兰台记忆（Lantai）的兴趣！以下是贡献指南。

## 开发环境搭建

### 前置要求

- Python 3.11+
- Git
- （可选）Docker
- （可选）uv（推荐的 Python 包管理器）

### 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/JIUSHIQINGSHAN/Lantai.git
cd Lantai

# 2. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# 3. 安装依赖
pip install -e ".[dev]"

# 4. 安装 pre-commit hooks
pre-commit install --install-hooks

# 5. 初始化数据库
python scripts/init_db.py

# 6. 运行测试
pytest tests/ -q
```

## 分支策略

- `master`：稳定分支，只接受经过测试的合并
- `feature/xxx`：新功能开发
- `fix/xxx`：Bug 修复

## 提交规范

本项目遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范。

### 提交格式

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

### 类型（Type）

| 类型 | 说明 |
|---|---|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `build` | 构建系统或外部依赖变更 |
| `chore` | 非业务性代码修改 |
| `ci` | 持续集成流程变更 |
| `docs` | 文档修改 |
| `style` | 代码样式调整（不影响功能） |
| `refactor` | 代码重构（不改变功能逻辑） |
| `perf` | 性能优化 |
| `test` | 测试用例修改 |
| `revert` | 回退提交 |

### 范围（Scope）

可选字段，用圆括号包围，描述变更涉及的模块。常用范围：

- `api`：API 路由
- `gate`：相关性闸门
- `evolution`：演化模块
- `memory`：记忆模块
- `storage`：存储层
- `retrieval`：检索模块
- `ingestion`：摄取模块
- `security`：安全相关
- `docs`：文档
- `ci`：CI/CD

### 示例

```
feat(evolution): add salience-based contradiction gate

Fog integration: when new fact conflicts with existing memory,
compare salience scores before overwrite. Higher salience wins.

Refs: #42
```

```
fix(storage): resolve SQLite deadlock in apply_proposal

Use outer session for MemoryEdge to prevent self-deadlock
under concurrent write operations.

Refs: #38
```

## 代码规范

### Python 风格

- 遵循 PEP 8
- 行长度：100 字符
- 使用 double quotes
- 排序 imports（ruff 自动处理）

### 类型注解

所有公共函数必须有类型注解：

```python
def search_memories(
    query: str,
    top_k: int = 10,
    trace: bool = False,
) -> SearchResults:
    ...
```

### 文档字符串

公共函数、类、模块必须包含文档字符串：

```python
def add_memory(title: str, content: str, lane: str) -> Memory:
    """Add a new memory entry.

    Args:
        title: Brief title for the memory.
        content: Full content of the memory.
        lane: Memory lane (fact/chat/preference).

    Returns:
        The created Memory object.
    """
```

## 测试

### 运行测试

```bash
# 运行全部测试
pytest tests/ -q

# 运行特定模块
pytest tests/test_gate.py -q

# 带覆盖率
pytest tests/ --cov=remembrance --cov-report=html
```

### 测试要求

- 新增功能必须包含对应测试
- Bug 修复必须包含回归测试
- 所有测试必须通过（120+ 测试）
- 测试命名清晰：`test_<feature>_<scenario>`

## PR 检查清单

提交 PR 前，请确认：

- [ ] 所有测试通过（`pytest tests/ -q`）
- [ ] pre-commit hooks 通过（自动运行）
- [ ] 提交信息符合 Conventional Commits 规范
- [ ] 新功能包含对应测试
- [ ] Bug 修复包含回归测试
- [ ] 文档已更新（README、CHANGELOG、ADR 等）
- [ ] 无敏感信息泄露（API keys、密码等）

## 版本发布流程

版本发布遵循 [Semantic Versioning](https://semver.org/)：

1. **更新版本号**：`pyproject.toml` 中的 `version`
2. **更新 CHANGELOG**：将 `[Unreleased]` 的变更为新版本
3. **提交变更**：`chore(release): v0.3.7`
4. **创建 tag**：`git tag v0.3.7`
5. **推送**：`git push origin master --tags`
6. **CI 自动构建**：GitHub Actions 自动构建并推送 Docker 镜像到 GHCR

### 版本号规则

- **MAJOR**：破坏性 API 变更（`feat!:` 或 `BREAKING CHANGE:`）
- **MINOR**：向后兼容的新功能（`feat:`）
- **PATCH**：向后兼容的 Bug 修复（`fix:`）

## 安全

发现安全漏洞？请勿公开 issue，直接联系维护者。

## 许可证

贡献即表示您同意您的贡献将以 MIT 许可证发布。
