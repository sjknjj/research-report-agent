"""研报 AI Agent（Research Report Agent）源码包。

模块划分：
- common:   配置、数据结构、日志等公共能力
- llm:      大模型客户端（OpenAI 兼容协议，内置离线兜底）
- tools:    检索 / 网页抓取等外部工具
- agent:    规划、检索、分析、撰写、审校等智能体与编排器
- export:   报告导出（Markdown / HTML）
"""

__version__ = "0.1.0"
