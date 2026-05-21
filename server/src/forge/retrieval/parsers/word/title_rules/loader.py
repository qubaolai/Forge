"""规则集 YAML 加载器.

从一个或多个 YAML 文件加载 TitleRule / ExcludeRule, 合并成 RuleSet.

YAML schema (单个文件):
    rules:
      - name: <str>
        pattern: <regex>
        level: <int 1-6>
        specificity: <int>          # 可选, 默认 level*10
        priority: <int>             # 可选, 默认 0
        require_bold: <strict|prefer|ignore>  # 可选, 默认 prefer
        max_length: <int>           # 可选, 默认 80

    excludes:
      - name: <str>
        pattern: <regex>

    forbidden_endings: [<char>, ...]  # 可选
    global_max_length: <int>          # 可选
    score_threshold: <float>          # 可选
    title_size_ratio: <float>         # 可选

    include:                          # 可选, 引入其他 YAML 文件
      - <relative_path.yaml>
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from .rule import ExcludeRule, RuleSet, TitleRule

logger = logging.getLogger(__name__)


class RuleSetLoader:
    """规则集加载器.

    支持单文件加载、多文件叠加、include 递归引用.
    重名规则后加载者覆盖先加载者.

    Attributes:
        base_dir: include 相对路径的解析基准目录.
    """

    def __init__(self, base_dir: Path | None = None):
        """初始化加载器.

        Args:
            base_dir: include 字段的相对路径基准目录.
                     None 时取被加载文件所在目录.
        """
        self.base_dir = base_dir

    def load(self, paths: list[Path]) -> RuleSet:
        """加载并合并多个 YAML 文件.

        Args:
            paths: YAML 文件路径列表.

        Returns:
            合并后的 RuleSet. paths 为空时返回空 RuleSet.
        """
        merged = RuleSet()
        seen_files: set[Path] = set()

        for p in paths:
            self._load_into(Path(p), merged, seen_files)

        # 重名规则后者覆盖前者: 用 dict 去重保留最后出现
        rules_by_name = {}
        for r in merged.rules:
            rules_by_name[r.name] = r
        merged.rules = list(rules_by_name.values())

        excl_by_name = {}
        for e in merged.excludes:
            excl_by_name[e.name] = e
        merged.excludes = list(excl_by_name.values())

        logger.info(
            "RuleSet 加载完成: %d 条规则, %d 条黑名单, threshold=%.2f",
            len(merged.rules),
            len(merged.excludes),
            merged.score_threshold,
        )
        return merged

    def _load_into(
        self,
        path: Path,
        target: RuleSet,
        seen: set[Path],
    ) -> None:
        """加载单个 YAML 到 target, 处理 include.

        Args:
            path: YAML 文件路径.
            target: 累积的 RuleSet, 会被原地修改.
            seen: 已加载的文件集合, 防循环引用.
        """
        path = path.resolve()
        if path in seen:
            return
        if not path.exists():
            logger.warning("规则文件不存在: %s", path)
            return
        seen.add(path)

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            logger.error("解析 YAML 失败 %s: %s", path, e)
            return

        # 先处理 include, 让被 include 的规则先入栈, 当前文件后入栈
        # 这样当前文件的同名规则会覆盖被 include 的
        base = self.base_dir or path.parent
        for inc in data.get("include") or []:
            inc_path = (base / inc).resolve()
            self._load_into(inc_path, target, seen)

        # 全局配置: 最后一次出现的胜出
        if "global_max_length" in data:
            target.global_max_length = int(data["global_max_length"])
        if "score_threshold" in data:
            target.score_threshold = float(data["score_threshold"])
        if "title_size_ratio" in data:
            target.title_size_ratio = float(data["title_size_ratio"])
        if "forbidden_endings" in data:
            target.forbidden_endings = list(data["forbidden_endings"])

        # rules
        ruleset_name = path.stem
        for raw in data.get("rules") or []:
            rule = self._build_rule(raw, ruleset_name)
            if rule is not None:
                target.rules.append(rule)

        # excludes
        for raw in data.get("excludes") or []:
            excl = self._build_exclude(raw)
            if excl is not None:
                target.excludes.append(excl)

    @staticmethod
    def _build_rule(raw: dict, ruleset_name: str) -> TitleRule | None:
        """从 dict 构造 TitleRule.

        Args:
            raw: YAML 中的一项规则.
            ruleset_name: 来源 yaml 的 stem.

        Returns:
            TitleRule, 字段非法时返回 None.
        """
        try:
            name = str(raw["name"])
            pattern = str(raw["pattern"])
            level = int(raw["level"])
        except (KeyError, ValueError, TypeError) as e:
            logger.error("规则字段缺失或非法: %s, %s", raw, e)
            return None

        if not 1 <= level <= 6:
            logger.error("规则 %s 的 level=%d 超出 1-6 范围", name, level)
            return None

        try:
            compiled = re.compile(pattern)
        except re.error as e:
            logger.error("规则 %s 的正则编译失败: %s", name, e)
            return None

        require_bold = raw.get("require_bold", "prefer")
        if require_bold not in ("strict", "prefer", "ignore"):
            logger.warning("规则 %s 的 require_bold=%s 非法, 改为 prefer", name, require_bold)
            require_bold = "prefer"

        return TitleRule(
            name=name,
            pattern=pattern,
            compiled=compiled,
            level=level,
            specificity=int(raw.get("specificity", level * 10)),
            priority=int(raw.get("priority", 0)),
            require_bold=require_bold,
            max_length=int(raw.get("max_length", 80)),
            rule_set=ruleset_name,
        )

    @staticmethod
    def _build_exclude(raw: dict) -> ExcludeRule | None:
        """从 dict 构造 ExcludeRule."""
        try:
            name = str(raw["name"])
            pattern = str(raw["pattern"])
        except (KeyError, ValueError, TypeError) as e:
            logger.error("黑名单字段缺失: %s, %s", raw, e)
            return None
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            logger.error("黑名单 %s 正则编译失败: %s", name, e)
            return None
        return ExcludeRule(name=name, pattern=pattern, compiled=compiled)
