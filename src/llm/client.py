"""大模型客户端。

设计目标：
1. 兼容 OpenAI Chat Completions 协议，因此 OpenAI / DeepSeek / 豆包方舟 /
   通义千问 / Kimi / 智谱 等任何提供 OpenAI 兼容端点的厂商都可通过
   配置 ``LLM_BASE_URL`` 与 ``LLM_MODEL`` 直接接入；
2. 仅使用标准库（urllib）发起请求，核心流程无需安装 openai SDK；
3. 内置离线兜底（offline）：未配置 API Key 时用确定性模板生成结构化内容，
   使整条流水线在无网络 / 无密钥时依然可以端到端跑通，便于演示与测试。

注意：离线模式产出的是“方法论模板 + 占位说明”，不会编造真实数据或来源。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..common.logger import get_logger

logger = get_logger("llm.client")


class LLMError(RuntimeError):
    """大模型调用异常。"""


# ---------------------------------------------------------------------------
# 离线兜底响应器
# ---------------------------------------------------------------------------
class OfflineResponder:
    """根据阶段（stage）与上下文生成确定性的占位响应。"""

    # 通用中文研报章节模板（heading, purpose, keywords）
    DEFAULT_SECTIONS = [
        ("核心观点速览", "提炼研究对象的核心结论与关键假设", ["核心观点", "结论"]),
        ("行业背景与发展历程", "梳理行业定义、发展阶段与关键里程碑", ["行业 发展历程", "行业 背景"]),
        ("市场规模与增长驱动", "分析市场容量、增速及其核心驱动因素", ["市场规模", "增长率 驱动因素"]),
        ("产业链与商业模式", "拆解上下游结构、价值分配与盈利模式", ["产业链", "商业模式"]),
        ("竞争格局与主要玩家", "对比主要参与者、市场份额与差异化定位", ["竞争格局", "头部企业 对比"]),
        ("风险与不确定性", "识别政策、技术、需求、经营等层面的风险", ["风险", "挑战 不确定性"]),
        ("结论与展望", "给出综合判断、趋势展望与跟踪指标", ["未来趋势", "展望 建议"]),
    ]

    def respond(
        self,
        stage: str,
        context: Optional[Dict[str, Any]],
        expect_json: bool,
    ) -> str:
        context = context or {}
        handler = {
            "plan": self._plan,
            "abstract": self._abstract,
            "section_analysis": self._section_analysis,
            "write_section": self._write_section,
            "review": self._review,
            "company_brief": self._company_brief,
            "announcement": self._announcement,
            "industry": self._industry,
        }.get(stage, self._plain)

        result = handler(context)
        if expect_json:
            return json.dumps(result, ensure_ascii=False)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)

    def _plan(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        topic = ctx.get("topic", "研究主题")
        sections = [
            {
                "heading": h,
                "purpose": p,
                "keywords": kws,
            }
            for (h, p, kws) in self.DEFAULT_SECTIONS
        ]
        questions = [
            f"{topic}所处赛道的发展阶段与核心驱动因素是什么？",
            f"{topic}的市场规模、增速与未来空间如何？",
            f"{topic}的产业链结构、商业模式与竞争格局如何？",
            f"{topic}面临哪些主要风险与不确定性？",
        ]
        return {
            "topic": topic,
            "objective": f"围绕“{topic}”形成一份结构完整、逻辑清晰的研究报告框架。",
            "questions": questions,
            "sections": sections,
            "keywords": [topic, f"{topic} 行业研究", f"{topic} 市场分析"],
        }

    def _abstract(self, ctx: Dict[str, Any]) -> str:
        topic = ctx.get("topic", "研究主题")
        headings = ctx.get("headings", [])
        lines = [
            "> ⚠️ 本报告由**离线演示模式**生成：未连接真实大模型与检索服务，",
            "> 以下内容为研报方法论模板与占位说明，不含真实数据，亦不构成任何投资建议。\n",
            f"本报告以“{topic}”为研究对象，按照 "
            f"{'、'.join(headings) if headings else '背景—分析—风险—结论'} "
            "的逻辑展开。",
            "在真实运行（配置 LLM_API_KEY）时，此处将由大模型综合检索证据，",
            "提炼出核心观点、关键数据与结论，并给出相应的跟踪指标与风险提示。",
        ]
        return "\n".join(lines)

    def _section_analysis(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        heading = ctx.get("heading", "章节")
        evidence = ctx.get("evidence", [])
        bullets = [
            f"【分析维度】{heading}：应先界定概念与口径，再给出量化判断与定性结论。",
            "【数据要求】关键数字需标注来源、时间与统计口径，并保证可交叉验证。",
        ]
        if evidence:
            bullets.append(
                "【已检索资料】离线模式不直接引用以下片段，在线模式将据此归纳并标注引用："
            )
            for idx, item in enumerate(evidence[:3], start=1):
                snippet = str(item).strip().replace("\n", " ")
                bullets.append(f"资料{idx}：{snippet[:80]}")
        else:
            bullets.append("【证据状态】当前未获得真实检索证据，在线模式将自动补齐。")
        # 优先使用上层传入的全局资料编号，缺省时退化为列表局部编号
        citations = ctx.get("citation_indexes")
        if not citations:
            citations = list(range(1, len(evidence) + 1))
        return {"bullet_points": bullets, "citation_indexes": citations[:5]}

    def _write_section(self, ctx: Dict[str, Any]) -> str:
        heading = ctx.get("heading", "章节")
        bullets = ctx.get("bullets", [])
        parts = [
            f"### {heading}\n",
            f"本节围绕“{heading}”展开（离线演示占位文本）。正式运行时，",
            "撰写智能体会基于分析要点与检索证据，输出连贯、有数据支撑的论述，",
            "并在关键论断处标注参考文献编号。\n",
        ]
        if bullets:
            parts.append("核心要点：\n")
            for b in bullets:
                parts.append(f"- {b}")
        return "\n".join(parts)

    def _review(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        section_count = int(ctx.get("section_count", 0))
        issues: List[str] = []
        passed = section_count > 0
        score = 80 if passed else 40
        if not passed:
            issues.append("报告缺少正文章节。")
        return {
            "passed": passed,
            "score": score,
            "issues": issues,
            "suggestions": ["在线模式下可进一步校验数据来源与引用一致性。"],
        }

    @staticmethod
    def _yi(value) -> str:
        try:
            return f"{float(value) / 1e8:.2f} 亿元"
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _pct(value) -> str:
        try:
            v = float(value)
            return ("+" if v > 0 else "") + f"{v:.2f}%"
        except (TypeError, ValueError):
            return "—"

    def _company_brief(self, ctx: Dict[str, Any]) -> str:
        name = ctx.get("name", "公司")
        period = ctx.get("period", "—")
        fin = ctx.get("financial", {})
        price = ctx.get("price", {})
        news = ctx.get("news", [])
        lines = [
            f"### {name} 投研速览",
            "> 离线演示：以下数字全部取自『已核实数据包』，未引入任何外部数据。",
            "",
            f"#### 一、财务表现（报告期：{period}）",
            f"- 营业总收入 {self._yi(fin.get('revenue_yuan'))}，同比 {self._pct(fin.get('revenue_yoy_pct'))}",
            f"- 归母净利润 {self._yi(fin.get('net_profit_yuan'))}，同比 {self._pct(fin.get('net_profit_yoy_pct'))}",
            f"- 销售毛利率 {self._pct(fin.get('gross_margin_pct'))}；"
            f"净资产收益率 {self._pct(fin.get('roe_pct'))}；基本每股收益 {fin.get('eps', '—')} 元",
            "",
            "#### 二、行情表现",
            f"- 区间 {price.get('start_date', '—')}~{price.get('end_date', '—')}，"
            f"共 {price.get('trading_days', '—')} 个交易日，最新收盘价 {price.get('latest_close', '—')} 元"
            f"（当日涨跌幅 {self._pct(price.get('latest_pct_change'))}），区间涨跌幅 {self._pct(price.get('period_return_pct'))}",
            f"- 区间最高 {price.get('period_high', '—')} 元 / 最低 {price.get('period_low', '—')} 元；"
            f"年化波动率 {self._pct(price.get('annual_volatility_pct'))}；{price.get('price_vs_ma20') or '均线数据—'}",
            "",
            "#### 三、公开资讯",
        ]
        if news:
            lines += [f"- {n.get('time', '')} {n.get('title', '')}" for n in news[:5]]
        else:
            lines.append("- 暂无可用资讯。")
        lines += [
            "",
            "#### 四、风险提示",
            "- 以上为离线模板结论，正式运行将由大模型结合事实包与资讯生成；本内容不构成投资建议。",
        ]
        return "\n".join(lines)

    def _announcement(self, ctx: Dict[str, Any]) -> str:
        title = ctx.get("title", "公告")
        excerpt = str(ctx.get("excerpt", "")).strip().replace("\n", " ")[:240]
        return "\n".join([
            f"### 公告要点提炼：{title}",
            "> 离线演示：以下为结构化提炼框架，数字一律以公告原文为准，不做任何外推。",
            "",
            "#### 1. 事件类型",
            "- 待在线模式结合公告原文判定（如业绩快报/定增/回购/重大合同等）。",
            "",
            "#### 2. 关键数据（原样引用，禁止改写）",
            f"- 公告原文摘录：{excerpt if excerpt else '（未提供原文）'}",
            "",
            "#### 3. 经营要点与潜在影响",
            "- 在线模式将提炼主营业务变化、同比变动原因与对业绩的影响路径。",
            "",
            "#### 4. 风险提示",
            "- 以公司正式披露文件为准；初步核算数据可能与最终年报存在差异。",
        ])

    def _industry(self, ctx: Dict[str, Any]) -> str:
        name = ctx.get("industry", "行业")
        stat = ctx.get("stats", {})
        return "\n".join([
            f"### {name} 行业汇总（离线演示）",
            "> 数字来自板块成分快照聚合，未引入外部数据。",
            "",
            f"- 纳入统计成分股：{stat.get('count', '—')} 家；上涨 {stat.get('up_count', '—')} 家",
            f"- 板块平均涨跌幅：{self._pct(stat.get('avg_pct'))}；中位数：{self._pct(stat.get('median_pct'))}",
            f"- 领涨个股：{stat.get('leaders', '—')}",
            "",
            "在线模式将进一步结合产业链、政策与需求侧信息形成行业研判。",
        ])

    def _plain(self, ctx: Dict[str, Any]) -> str:
        return "（离线演示模式）当前为占位响应；配置 LLM_API_KEY 后将由真实大模型生成。"


# ---------------------------------------------------------------------------
# 主客户端
# ---------------------------------------------------------------------------
class LLMClient:
    """OpenAI 兼容的对话补全客户端，带离线兜底。"""

    def __init__(self, settings: Any):
        self.settings = settings
        self.offline = settings.effective_mode == "offline"
        self._responder = OfflineResponder()
        if self.offline:
            logger.warning(
                "未检测到 LLM_API_KEY（或显式指定 offline），已进入离线演示模式；"
                "配置密钥后可获得真实分析结果。"
            )

    # -- 对外主入口 ---------------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        stage: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        expect_json: bool = False,
        temperature: Optional[float] = None,
    ) -> str:
        """发起一次对话补全，返回文本。

        :param messages: OpenAI 风格消息列表 [{"role", "content"}]
        :param stage: 流水线阶段，仅供离线兜底选择模板
        :param context: 结构化上下文，仅供离线兜底生成更贴切的占位内容
        :param expect_json: 是否期望 JSON（离线时据此返回 JSON 字符串）
        """
        if self.offline:
            return self._responder.respond(stage or "", context, expect_json)
        return self._request_online(messages, temperature=temperature)

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        *,
        stage: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
    ) -> Any:
        """发起对话并把结果解析为 JSON 对象（dict/list）。"""
        raw = self.chat(
            messages,
            stage=stage,
            context=context,
            expect_json=True,
            temperature=temperature,
        )
        return self.parse_json(raw)

    # -- 在线请求 -----------------------------------------------------------
    def _request_online(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
    ) -> str:
        if not self.settings.llm_api_key:
            raise LLMError(
                "在线模式缺少 LLM_API_KEY，请在 .env 中配置或将 LLM_MODE 设为 offline。"
            )
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.temperature
            if temperature is None
            else temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.llm_api_key}",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.settings.request_timeout
            ) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:500]
            raise LLMError(
                f"调用大模型失败：HTTP {exc.code} {exc.reason}；响应：{detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"无法连接大模型服务 {url}：{exc.reason}") from exc

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"大模型返回结构异常：{body}") from exc

    # -- 工具 ---------------------------------------------------------------
    @staticmethod
    def parse_json(raw: str) -> Any:
        """从模型输出中稳健地提取 JSON。

        依次尝试：直接解析 → 提取 ```json 代码块 → 截取首个 {/[ 到末尾。
        """
        raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        fence = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.DOTALL)
        if fence:
            try:
                return json.loads(fence.group(1).strip())
            except json.JSONDecodeError:
                raw = fence.group(1).strip()

        start_candidates = [idx for idx in (raw.find("{"), raw.find("[")) if idx >= 0]
        if start_candidates:
            start = min(start_candidates)
            open_ch = raw[start]
            close_ch = "}" if open_ch == "{" else "]"
            end = raw.rfind(close_ch)
            if end > start:
                try:
                    return json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    pass
        raise LLMError(f"无法把模型输出解析为 JSON：{raw[:300]}")
