"""动态总结服务 - 使用AI为每条动态生成精简总结，用于智能上下文模式"""
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy import select, func as sa_func
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.qq_post import QQPost, QQComment
from app.models.xhs_post import XHSNote, XHSComment
from app.models.post_summary import PostSummary

logger = logging.getLogger(__name__)

# 每批次总结的记录数（避免一次性调用太多AI）
BATCH_SIZE = 10
# 单条总结的最大长度
MAX_SUMMARY_LENGTH = 300


class SummaryService:
    """动态总结服务"""

    async def generate_summaries(self, force_all: bool = False) -> dict:
        """为所有未总结的动态生成AI总结。

        Args:
            force_all: 如果为True，重新总结所有记录（包括已总结的）

        Returns:
            统计信息 {new, updated, failed, total}
        """
        stats = {"new": 0, "updated": 0, "failed": 0, "skipped": 0, "total": 0}

        async with async_session() as db:
            # 获取所有已有总结的映射 {(platform, post_db_id): PostSummary}
            existing_result = await db.execute(select(PostSummary))
            existing_map = {
                (s.platform, s.post_db_id): s
                for s in existing_result.scalars().all()
            }

            # ===== 处理QQ动态 =====
            qq_result = await db.execute(
                select(QQPost)
                .options(selectinload(QQPost.comments))
                .order_by(QQPost.post_time)
            )
            qq_posts = qq_result.scalars().all()

            # ===== 处理小红书笔记 =====
            xhs_result = await db.execute(
                select(XHSNote)
                .options(selectinload(XHSNote.comments))
                .order_by(XHSNote.post_time)
            )
            xhs_notes = xhs_result.scalars().all()

        # 收集需要处理的记录
        to_process = []

        for post in qq_posts:
            content = (post.content or "").strip()
            if not content and not post.images and not post.video_url:
                continue
            existing = existing_map.get(("qq", post.id))
            if existing and not force_all:
                # 检查原始记录是否有更新
                if existing.source_updated_at and post.updated_at:
                    if post.updated_at <= existing.source_updated_at:
                        stats["skipped"] += 1
                        continue
                else:
                    stats["skipped"] += 1
                    continue
            to_process.append(("qq", post))

        for note in xhs_notes:
            content = f"{note.title or ''}\n{note.content or ''}".strip()
            if not content and not note.images and not note.video_url:
                continue
            existing = existing_map.get(("xhs", note.id))
            if existing and not force_all:
                if existing.source_updated_at and note.updated_at:
                    if note.updated_at <= existing.source_updated_at:
                        stats["skipped"] += 1
                        continue
                else:
                    stats["skipped"] += 1
                    continue
            to_process.append(("xhs", note))

        stats["total"] = len(to_process)
        logger.info(f"总结任务: 需处理{len(to_process)}条记录, 已跳过{stats['skipped']}条")

        if not to_process:
            return stats

        # 分批处理
        for i in range(0, len(to_process), BATCH_SIZE):
            batch = to_process[i:i + BATCH_SIZE]
            await self._process_batch(batch, existing_map, stats)

        logger.info(f"总结完成: 新增{stats['new']}, 更新{stats['updated']}, 失败{stats['failed']}")
        return stats

    async def _process_batch(self, batch: list, existing_map: dict, stats: dict):
        """处理一批记录的总结"""
        from app.services.ai_service import ai_service

        # 构建批量总结prompt
        records_text = []
        for idx, (platform, record) in enumerate(batch):
            if platform == "qq":
                content = (record.content or "").strip()
                comments_text = ""
                if record.comments:
                    comments_text = " | 评论: " + "; ".join(
                        f"{c.author_nickname or '匿名'}: {(c.content or '')[:100]}"
                        for c in record.comments[:5]
                    )
                forward = f" | 转发: {record.forward_content[:200]}" if record.forward_content else ""
                images_info = f" | {len(record.images)}张图片" if record.images else ""
                video_info = " | 有视频" if record.video_url or record.local_video_path else ""
                records_text.append(
                    f"[{idx}] QQ动态 | {record.author_nickname or record.qq_number} | "
                    f"{record.post_time.strftime('%Y-%m-%d') if record.post_time else '?'}"
                    f"{images_info}{video_info}\n{content}{forward}{comments_text}"
                )
            else:
                title = (record.title or "").strip()
                content = (record.content or "").strip()
                full_text = f"标题:{title}\n{content}" if title else content
                comments_text = ""
                if record.comments:
                    comments_text = " | 评论: " + "; ".join(
                        f"{c.author_nickname or '匿名'}: {(c.content or '')[:100]}"
                        for c in record.comments[:5]
                    )
                tags = f" | 标签: {','.join(record.tags[:5])}" if record.tags else ""
                images_info = f" | {len(record.images)}张图片" if record.images else ""
                video_info = " | 有视频" if record.video_url or record.local_video_path else ""
                records_text.append(
                    f"[{idx}] 小红书笔记 | {record.author_nickname or record.xhs_uid} | "
                    f"{record.post_time.strftime('%Y-%m-%d') if record.post_time else '?'}"
                    f"{images_info}{video_info}{tags}\n{full_text}{comments_text}"
                )

        prompt = f"""请为以下每条社交动态生成精简总结。每条总结应包含：
1. 核心内容/主题（这条动态说了什么）
2. 情绪/态度（积极/消极/中性）
3. 值得注意的细节（如有）

要求：
- 每条总结控制在2-3句话内，不超过{MAX_SUMMARY_LENGTH}字
- 保留关键信息，如人名、地点、事件、情感
- 如果有图片/视频但无法看到内容，注明"附有N张图片/视频"
- 返回格式：每条用 [编号] 开头，一行一条总结

动态内容：
{"".join(chr(10) + t for t in records_text)}"""

        try:
            client = await ai_service._get_client()
            response = await client.chat.completions.create(
                model=ai_service._config.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.3,
            )
            result_text = response.choices[0].message.content.strip()

            # 解析AI返回的总结
            summaries = self._parse_batch_summaries(result_text, len(batch))

            # 保存到数据库
            async with async_session() as db:
                for idx, (platform, record) in enumerate(batch):
                    summary_text = summaries.get(idx)
                    if not summary_text:
                        stats["failed"] += 1
                        continue

                    existing = existing_map.get((platform, record.id))
                    if existing:
                        # 更新已有总结
                        update_result = await db.execute(
                            select(PostSummary).where(
                                PostSummary.platform == platform,
                                PostSummary.post_db_id == record.id,
                            )
                        )
                        db_summary = update_result.scalar_one_or_none()
                        if db_summary:
                            db_summary.summary = summary_text[:MAX_SUMMARY_LENGTH]
                            db_summary.source_updated_at = record.updated_at or datetime.now()
                            db_summary.summary_version = (db_summary.summary_version or 1) + 1
                            db_summary.author = self._get_author(platform, record)
                            stats["updated"] += 1
                        else:
                            stats["failed"] += 1
                            continue
                    else:
                        # 新建总结
                        db_summary = PostSummary(
                            platform=platform,
                            post_db_id=record.id,
                            post_original_id=getattr(record, "post_id", None) or getattr(record, "note_id", None),
                            author=self._get_author(platform, record),
                            post_time=record.post_time,
                            summary=summary_text[:MAX_SUMMARY_LENGTH],
                            images_count=len(record.images) if record.images else 0,
                            has_video=bool(record.video_url or record.local_video_path),
                            source_updated_at=record.updated_at or datetime.now(),
                        )
                        db.add(db_summary)
                        # 更新缓存
                        existing_map[(platform, record.id)] = db_summary
                        stats["new"] += 1

                await db.commit()

        except Exception as e:
            logger.error(f"批量总结失败: {e}", exc_info=True)
            stats["failed"] += len(batch)

    def _parse_batch_summaries(self, text: str, expected_count: int) -> dict[int, str]:
        """解析AI返回的批量总结文本"""
        summaries = {}
        current_idx = None
        current_lines = []

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            # 尝试匹配 [0], [1] 等编号
            import re
            match = re.match(r'^\[(\d+)\]\s*(.*)', line)
            if match:
                # 保存上一条
                if current_idx is not None and current_lines:
                    summaries[current_idx] = " ".join(current_lines).strip()
                current_idx = int(match.group(1))
                remainder = match.group(2).strip()
                current_lines = [remainder] if remainder else []
            elif current_idx is not None:
                current_lines.append(line)

        # 保存最后一条
        if current_idx is not None and current_lines:
            summaries[current_idx] = " ".join(current_lines).strip()

        return summaries

    def _get_author(self, platform: str, record) -> str:
        if platform == "qq":
            return record.author_nickname or record.qq_number or "未知"
        return record.author_nickname or record.xhs_uid or "未知"

    async def get_summary_stats(self) -> dict:
        """获取总结统计信息"""
        async with async_session() as db:
            summary_count = (await db.execute(
                select(sa_func.count()).select_from(PostSummary)
            )).scalar() or 0
            qq_total = (await db.execute(
                select(sa_func.count()).select_from(QQPost)
            )).scalar() or 0
            xhs_total = (await db.execute(
                select(sa_func.count()).select_from(XHSNote)
            )).scalar() or 0

            # 最近总结时间
            latest = (await db.execute(
                select(PostSummary.updated_at)
                .order_by(PostSummary.updated_at.desc())
                .limit(1)
            )).scalar()

            return {
                "summarized": summary_count,
                "qq_total": qq_total,
                "xhs_total": xhs_total,
                "total": qq_total + xhs_total,
                "coverage": round(summary_count / max(qq_total + xhs_total, 1) * 100, 1),
                "last_summary_time": latest.isoformat() if latest else None,
            }


summary_service = SummaryService()
