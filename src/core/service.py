"""
CodeCritic — 审查核心服务
=========================
封装完整的代码审查流程，通过依赖注入提供上下文。
避免全局变量，职责单一，便于测试和扩展。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.graph.builder import build_graph
from src.graph.state import CodeCriticState
from src.output.report_service import ReportService
from src.utils.config_loader import load_settings
from src.utils.logger import logger
from src.utils.path_utils import safe_resolve_path


class FileReadError(Exception):
    """文件读取异常（编码错误等）"""


class ReviewService:
    """
    代码审查核心服务。

    职责：
    - 构建 LangGraph 并执行审查流程
    - 通过依赖注入提供 console / settings / report_service
    - 路径安全解析（防止遍历攻击）

    使用方式：
        service = ReviewService()
        result = service.run_review(code="def foo(): pass")
        service.report_service.print_report(result, ["markdown"])

    测试时可注入 mock：
        service = ReviewService(console=MockConsole(), settings=test_settings)
    """

    # 尝试的编码顺序（覆盖常见中英文项目）
    _FALLBACK_ENCODINGS = ["utf-8", "gbk", "gb2312", "utf-16", "latin-1"]

    def __init__(
        self,
        settings: Optional[dict[str, Any]] = None,
        console: Optional[Console] = None,
    ):
        self.settings = settings if settings is not None else load_settings()
        self._console = console
        self._report_service: Optional[ReportService] = None
        self._graph = None

    # ── 依赖属性（延迟初始化） ──────────────────────────────

    @property
    def console(self) -> Console:
        """控制台输出（按需创建，方便测试注入 mock）"""
        if self._console is None:
            self._console = Console()
        return self._console

    @console.setter
    def console(self, value: Console) -> None:
        self._console = value

    @property
    def report_service(self) -> ReportService:
        """报告输出服务"""
        if self._report_service is None:
            self._report_service = ReportService(self.settings, self.console)
        return self._report_service

    @report_service.setter
    def report_service(self, value: ReportService) -> None:
        self._report_service = value

    @property
    def graph(self):
        """LangGraph 图（延迟构建，避免重复初始化）"""
        if self._graph is None:
            self._graph = build_graph()
        return self._graph

    # ── 路径安全 ────────────────────────────────────────────

    @staticmethod
    def safe_path(path_str: str, must_exist: bool = True) -> Path:
        """
        安全解析用户输入的文件路径（委托给 path_utils.safe_resolve_path）。
        默认限制在当前工作目录下，防止路径遍历攻击。

        额外防御：在入口处再检查一次 resolved 路径不含遍历片段。
        """
        result = safe_resolve_path(
            path_str,
            allowed_base=Path.cwd(),
            must_exist=must_exist,
            must_be_file=True,
        )
        # 防御性检查：确保 os.path.normpath 已消除所有 .. 遍历
        if ".." in str(result):
            raise PermissionError(
                f"路径包含未解析的遍历片段: {path_str}"
            )
        return result

    # ── 文件读取 ────────────────────────────────────────────

    @classmethod
    def read_file(cls, file_path: Path) -> str:
        """
        安全读取文本文件，自动尝试常见编码。

        Args:
            file_path: 已通过 ``safe_path`` 验证的路径。

        Returns:
            文件内容字符串。

        Raises:
            FileReadError: 所有编码尝试均失败。
        """
        for enc in cls._FALLBACK_ENCODINGS:
            try:
                return file_path.read_text(encoding=enc)
            except UnicodeDecodeError:
                continue
        raise FileReadError(
            f"无法解码文件: {file_path}，"
            f"已尝试编码: {', '.join(cls._FALLBACK_ENCODINGS)}"
        )

    # ── 核心审查流程 ────────────────────────────────────────

    def _build_initial_state(
        self,
        code: str,
        language: str = "python",
        file_path: Optional[str] = None,
        skip_debate: bool = False,
        session_id: str = "",
        memory_enabled: bool = True,
        **extra_state,
    ) -> CodeCriticState:
        """构建审查初始状态（配置化，新增 Agent 无需修改此处）"""
        state = {
            "code": code,
            "code_language": language,
            "file_path": file_path,
            "skip_debate": skip_debate,
            "session_id": session_id,
            "memory_enabled": memory_enabled,
            "context": {"language": language},
        }
        # 合并额外状态（用于 diff_mode 等扩展）
        state.update(extra_state)
        return state

    def _execute_graph(
        self, initial_state: CodeCriticState
    ) -> CodeCriticState:
        """执行 LangGraph 图，带进度条显示"""
        run_config = {
            "configurable": {"thread_id": f"codecritic_{int(time.time())}"}
        }

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
            disable=not self.settings.get("ui", {}).get("show_progress", True),
        ) as progress:
            task = progress.add_task("代码评审进行中...", total=None)
            try:
                result = self.graph.invoke(initial_state, run_config)
            except Exception as e:
                logger.error(f"执行出错: {e}")
                self.console.print(f"[red]❌ 执行出错: {e}[/]")
                raise
            finally:
                progress.remove_task(task)

        return result

    def run_review(
        self,
        code: str,
        language: str = "python",
        file_path: Optional[str] = None,
        skip_debate: bool = False,
        session_id: str = "",
        memory_enabled: bool = True,
        **extra_state,
    ) -> CodeCriticState:
        """
        运行完整的代码审查流程。

        通过参数配置审查行为，新增 Agent 或审查阶段
        只需调整 ``build_graph()`` 配置，无需修改此方法（开闭原则）。

        Args:
            code: 源代码文本
            language: 代码语言
            file_path: 文件路径
            skip_debate: 是否跳过辩论
            session_id: 会话 ID（用于记忆关联）
            memory_enabled: 是否启用记忆
            **extra_state: 扩展 state 字段（如 diff_mode=True, diff_text=...）
        """
        initial_state = self._build_initial_state(
            code=code,
            language=language,
            file_path=file_path,
            skip_debate=skip_debate,
            session_id=session_id,
            memory_enabled=memory_enabled,
            **extra_state,
        )
        return self._execute_graph(initial_state)
