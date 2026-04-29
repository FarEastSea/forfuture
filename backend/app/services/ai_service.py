import logging
import re
from typing import Optional, AsyncGenerator
from openai import AsyncOpenAI
from sqlalchemy import select

logger = logging.getLogger(__name__)

# 图片URL提取正则
_IMAGE_RE = re.compile(r'\[图片\d+\]\s+(.+)')
# 单次对话最大图片数（避免token爆炸）
MAX_VISION_IMAGES = 50


class AIService:
    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None
        self._config = None

    async def _get_client(self) -> AsyncOpenAI:
        from app.database import async_session
        from app.models.ai_config import AIConfig

        async with async_session() as db:
            result = await db.execute(select(AIConfig).where(AIConfig.is_active == True))
            config = result.scalar_one_or_none()
            if not config:
                raise Exception("未配置AI服务，请先在设置中添加并激活AI配置")
            self._config = config
            return AsyncOpenAI(base_url=config.api_base, api_key=config.api_key)

    async def chat_stream(self, user_message: str, history: list, session_id: int) -> AsyncGenerator:
        client = await self._get_client()

        # 读取上下文模式配置
        from app.database import async_session
        from app.models.system_config import SystemConfig
        context_mode = "full"  # 默认全量
        vision_enabled = False
        server_base_url = ""
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(SystemConfig).where(
                        SystemConfig.key.in_([
                            "ai_context_mode", "ai_vision_enabled", "server_base_url"
                        ])
                    )
                )
                for config in result.scalars().all():
                    if config.key == "ai_context_mode" and config.value:
                        context_mode = config.value
                    elif config.key == "ai_vision_enabled":
                        vision_enabled = config.value == "true"
                    elif config.key == "server_base_url" and config.value:
                        server_base_url = config.value.rstrip("/")
        except Exception:
            pass

        # 获取动态记录上下文
        from app.services.rag_service import rag_service

        if context_mode == "smart_search":
            context_text, sources, stats = await rag_service.get_search_context(user_message)
        elif context_mode == "smart_summary":
            context_text, sources, stats = await rag_service.get_summary_context()
        elif context_mode == "auto":
            context_text, sources, stats = await rag_service.get_context_auto(user_message)
        else:
            # 默认full模式：全量上下文
            context_text, sources, stats = await rag_service.get_all_records_for_context()
            # 全量被截断时自动降级
            if stats.get("truncated", 0) > 0:
                logger.info(f"全量模式被截断{stats['truncated']}条，自动降级到智能模式")
                context_text, sources, stats = await rag_service.get_context_auto(user_message)

        mode_label = stats.get("mode", context_mode)
        mode_desc = {
            "full": "全量模式（包含所有动态完整内容）",
            "summary": "总结模式（使用AI生成的动态总结）",
            "search": "搜索模式（根据问题匹配相关动态）",
        }.get(mode_label, mode_label)

        stats_summary = f"QQ动态{stats.get('qq_count', 0)}条 + 小红书笔记{stats.get('xhs_count', 0)}条"
        if stats.get("total"):
            stats_summary = f"共{stats['total']}条记录"
        if stats.get("truncated"):
            stats_summary += f"（截断了{stats['truncated']}条较早的记录）"
        if stats.get("unsummarized"):
            stats_summary += f"（{stats['unsummarized']}条尚未总结）"

        # 图片感知能力描述（根据vision开关动态调整）
        if vision_enabled:
            image_capability = "5. **图片理解**：你可以直接看到动态中附带的图片内容，可以描述图片展示的场景、分析图中的情绪/环境/物品等"
        else:
            image_capability = "5. **图片感知**：上下文中包含了每条动态的图片URL，你虽然无法直接看到图片内容，但可以知道每条动态附带了哪些图片，可以引用图片URL让用户查看"

        system_prompt = f"""你是一个深度了解用户社交动态的个人AI助手。你拥有用户收集的全部QQ空间动态和小红书笔记记录，能够全面分析、总结、推理这些动态背后的信息。

📊 当前知识库概况：{stats_summary}
📋 上下文模式：{mode_desc}

以下是按时间顺序排列的动态记录：
━━━━━━━━━━━━━━━━━━━━━━━━
{context_text}
━━━━━━━━━━━━━━━━━━━━━━━━

你的能力和职责：
1. **全面分析能力**：你可以综合所有动态记录来回答问题，包括情绪分析、行为模式识别、性格推断、关系分析等
2. **时间线推理**：你可以根据时间线追踪变化趋势，如情绪变化、兴趣转移、生活状态变化等
3. **跨平台关联**：你可以关联QQ空间和小红书的内容进行综合分析
4. **深度推理**：对于模糊的问题（如"她是不是生病了"、"她最近开心吗"），你应该从全部动态中寻找蛛丝马迹——情绪词汇、发布频率变化、内容主题变化、互动数据变化等
{image_capability}
6. **互动数据分析**：点赞数、评论数、收藏数、分享数可以反映内容热度和社交互动情况

回答规则：
- 回答务必基于实际记录内容，不要虚构信息
- 涉及推测性分析时，明确标注"根据动态推测"或"从记录来看"
- 引用具体动态时注明来源（平台、发布者、大致时间）
- 如果记录中确实没有相关信息，坦诚告知，但可以说明已翻看了全部记录
- 保持友好、有温度的回答风格，像一个了解情况的朋友在分析"""

        # 构建消息列表（支持vision多模态格式）
        if vision_enabled and server_base_url:
            # 提取上下文中的图片URL，转换为vision API格式
            image_urls = _IMAGE_RE.findall(context_text)
            vision_image_parts = []
            for img_path in image_urls[:MAX_VISION_IMAGES]:
                img_path = img_path.strip()
                if img_path.startswith(("http://", "https://")):
                    full_url = img_path
                elif img_path.startswith("/static/"):
                    full_url = f"{server_base_url}{img_path}"
                else:
                    continue
                vision_image_parts.append({
                    "type": "image_url",
                    "image_url": {"url": full_url, "detail": "low"}
                })

            if vision_image_parts:
                # 多模态格式：system message 用 text + image_url 数组
                system_content = [{"type": "text", "text": system_prompt}] + vision_image_parts
                logger.info(f"AI对话启用图像理解: 传入{len(vision_image_parts)}张图片")
            else:
                system_content = system_prompt
        else:
            system_content = system_prompt

        messages = [{"role": "system", "content": system_content}]
        for msg in history[-20:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": user_message})

        try:
            stream = await client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature / 10.0,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

            # 最后返回sources和stats
            yield {"sources": sources, "context_stats": stats}
        except Exception as e:
            logger.error(f"AI对话错误: {e}")
            raise

    async def analyze_for_task(self, task_description: str, recent_records: list[dict],
                              task_type: str = "recurring", since_time: str = None) -> dict:
        """定时任务AI分析（支持时效性任务和智能频率建议）"""
        client = await self._get_client()

        records_text = ""
        for i, r in enumerate(recent_records):
            records_text += f"\n[{i}] [{r['platform']}] {r['author']} ({r['time']}):\n{r['content'][:800]}\n"
            if r.get("images"):
                records_text += f"  (附带 {len(r['images'])} 张图片)\n"

        temporal_ctx = ""
        if task_type == "temporal":
            temporal_ctx = f"""
⚠ 这是一个【时效性/事件驱动型】任务。
- 只关注最新的动态，旧消息不算触发条件
- {"仅分析 " + since_time + " 之后发布的动态" if since_time else "这是首次检查，分析所有最近动态"}
- 如果没有符合条件的新动态，triggered 应为 false
- 一旦触发后可以适当延长检查间隔避免重复打扰"""

        prompt = f"""你是一个智能社交动态监控助手。用户设置了以下监控任务：

任务描述：{task_description}
任务类型：{task_type}（{'事件驱动，只关注新动态' if task_type == 'temporal' else '定期汇报' if task_type == 'recurring' else '一次性查询'}）
{temporal_ctx}
以下是获取到的社交动态记录：
{records_text}

请分析这些记录，判断是否满足任务描述中的触发条件。

请以JSON格式回复：
{{
    "triggered": true/false,
    "reason": "详细的判断理由",
    "summary": "如果触发，给出事件摘要",
    "related_records": [相关记录的索引号，从0开始],
    "severity": "low/medium/high",
    "suggested_message": "建议发送给用户的完整消息内容（友好自然的语气，像朋友在告诉你）",
    "suggested_interval_minutes": 建议的下次检查间隔（分钟数，整数）
}}

重要规则：
1. 只根据实际记录内容判断，不要猜测或虚构
2. 如果没有明确证据表明满足条件，triggered应为false
3. severity: high=非常紧急需立即知道, medium=重要但不紧急, low=一般信息
4. suggested_interval_minutes按紧急性建议：
   - 紧急/健康类：15~30分钟
   - 一般重要：60~120分钟
   - 低优先级：120~360分钟
   - 刚触发后适当延长以免重复打扰
5. suggested_message语气要自然友好"""

        try:
            response = await client.chat.completions.create(
                model=self._config.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.3,
            )
            content = response.choices[0].message.content
            import json
            # 尝试提取JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content.strip())
        except Exception as e:
            logger.error(f"AI分析错误: {e}")
            return {"triggered": False, "reason": f"分析出错: {str(e)}"}

    async def parse_task_type_and_interval(self, description: str) -> dict:
        """用AI分析任务描述，判断任务类型和建议检查频率"""
        try:
            client = await self._get_client()
            response = await client.chat.completions.create(
                model=self._config.model,
                messages=[{
                    "role": "user",
                    "content": f"""分析以下监控任务描述，判断任务类型并建议检查频率。
只返回JSON，无其他文字。

任务描述：{description}

JSON格式：
{{
    "task_type": "temporal 或 recurring 或 oneoff",
    "reason": "判断理由",
    "suggested_interval_minutes": 检查间隔分钟数（整数），
    "cron_expr": "如果适合用cron表达式则返回5段式cron，否则为null"
}}

任务类型说明：
- temporal: 事件驱动型（如"她生病了告诉我"、"有人提到我通知我"），关注新动态中是否出现特定事件
- recurring: 定期汇报型（如"每天总结朋友圈"、"每周汇报动态"），按固定频率执行
- oneoff: 一次性查询（如"查一下她最近发了什么"），执行一次后可以停止"""
                }],
                max_tokens=300,
                temperature=0.1,
            )
            content = response.choices[0].message.content.strip()
            import json
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content.strip())
        except Exception as e:
            logger.error(f"解析任务类型失败: {e}")
            return {"task_type": "recurring", "suggested_interval_minutes": 120, "cron_expr": None}

    async def parse_cron_from_description(self, description: str) -> Optional[str]:
        """用AI从自然语言描述中解析cron表达式"""
        try:
            client = await self._get_client()
            response = await client.chat.completions.create(
                model=self._config.model,
                messages=[{
                    "role": "user",
                    "content": f"请根据以下任务描述，推断一个合理的执行频率，并返回cron表达式。只返回cron表达式本身，不要其他内容。\n\n任务描述：{description}\n\n如果无法确定频率，返回：0 */2 * * *（每2小时）"
                }],
                max_tokens=50,
                temperature=0.1,
            )
            cron = response.choices[0].message.content.strip()
            # 简单验证cron格式
            parts = cron.split()
            if len(parts) == 5:
                return cron
            return None
        except Exception:
            return None

    async def test_connection(self, api_base: str, api_key: str, model: str) -> str:
        client = AsyncOpenAI(base_url=api_base, api_key=api_key)
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Hi, reply with 'OK' only."}],
            max_tokens=10,
        )
        return f"连接成功，模型响应: {response.choices[0].message.content}"


ai_service = AIService()
