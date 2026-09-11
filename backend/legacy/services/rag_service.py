import logging
from sqlalchemy import select, desc, or_, func, asc
from legacy.database import async_session
from legacy.models.qq_post import QQPost, QQComment
from legacy.models.xhs_post import XHSNote, XHSComment

logger = logging.getLogger(__name__)

# 全量上下文总字符预算（给system prompt + 对话历史留空间）
# 大多数模型至少128K上下文，留足空间
MAX_CONTEXT_CHARS = 120000


class RAGService:
    """基于关键词的检索服务（pgvector向量检索可后续扩展）"""

    async def search(self, query: str, top_k: int = 10) -> list[dict]:
        """搜索相关记录。关键词匹配不足时回退到最近记录。"""
        results = []
        keywords = self._extract_keywords(query)

        async with async_session() as db:
            # 搜索QQ动态
            qq_query = select(QQPost).order_by(desc(QQPost.post_time)).limit(top_k * 2)
            if keywords:
                conditions = [QQPost.content.ilike(f"%{kw}%") for kw in keywords]
                qq_query = qq_query.where(or_(*conditions))
            qq_result = await db.execute(qq_query)
            for post in qq_result.scalars().all():
                results.append({
                    "platform": "qq",
                    "id": post.id,
                    "author": post.author_nickname or post.qq_number,
                    "content": post.content or "",
                    "images": post.images or [],
                    "time": post.post_time.isoformat() if post.post_time else "",
                    "score": self._calc_relevance(query, post.content or ""),
                })

            # 搜索小红书笔记
            xhs_query = select(XHSNote).order_by(desc(XHSNote.post_time)).limit(top_k * 2)
            if keywords:
                conditions = [
                    or_(
                        XHSNote.content.ilike(f"%{kw}%"),
                        XHSNote.title.ilike(f"%{kw}%"),
                    )
                    for kw in keywords
                ]
                xhs_query = xhs_query.where(or_(*conditions))
            xhs_result = await db.execute(xhs_query)
            for note in xhs_result.scalars().all():
                results.append({
                    "platform": "xhs",
                    "id": note.id,
                    "author": note.author_nickname or note.xhs_uid,
                    "content": f"{note.title or ''}\n{note.content or ''}".strip(),
                    "images": note.images or [],
                    "time": note.post_time.isoformat() if note.post_time else "",
                    "score": self._calc_relevance(query, f"{note.title or ''} {note.content or ''}"),
                })

            # 关键词搜索结果不足时，补充最近的记录作为上下文
            if len(results) < 3 and keywords:
                existing_ids_qq = {r["id"] for r in results if r["platform"] == "qq"}
                existing_ids_xhs = {r["id"] for r in results if r["platform"] == "xhs"}
                fallback_limit = top_k - len(results)

                # 补充最近QQ记录
                recent_qq = await db.execute(
                    select(QQPost).order_by(desc(QQPost.post_time)).limit(fallback_limit)
                )
                for post in recent_qq.scalars().all():
                    if post.id not in existing_ids_qq:
                        results.append({
                            "platform": "qq",
                            "id": post.id,
                            "author": post.author_nickname or post.qq_number,
                            "content": post.content or "",
                            "images": post.images or [],
                            "time": post.post_time.isoformat() if post.post_time else "",
                            "score": 0.1,  # 低分标记为回退结果
                        })

                # 补充最近XHS记录
                recent_xhs = await db.execute(
                    select(XHSNote).order_by(desc(XHSNote.post_time)).limit(fallback_limit)
                )
                for note in recent_xhs.scalars().all():
                    if note.id not in existing_ids_xhs:
                        results.append({
                            "platform": "xhs",
                            "id": note.id,
                            "author": note.author_nickname or note.xhs_uid,
                            "content": f"{note.title or ''}\n{note.content or ''}".strip(),
                            "images": note.images or [],
                            "time": note.post_time.isoformat() if note.post_time else "",
                            "score": 0.1,
                        })

                if len(results) > len(existing_ids_qq) + len(existing_ids_xhs):
                    logger.info(f"RAG关键词'{','.join(keywords)}'匹配不足，已补充最近记录（共{len(results)}条）")

        # 按相关度排序
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    async def get_recent_records(self, limit: int = 50, since: "datetime | None" = None) -> list[dict]:
        """获取最近的所有记录（用于定时任务分析）
        
        Args:
            limit: 最大返回记录数
            since: 如果指定，只返回此时间之后的记录（时效性任务用）
        """
        results = []
        async with async_session() as db:
            qq_query = select(QQPost).order_by(desc(QQPost.post_time)).limit(limit)
            if since:
                qq_query = qq_query.where(QQPost.post_time > since)
            qq_result = await db.execute(qq_query)
            for post in qq_result.scalars().all():
                results.append({
                    "platform": "qq",
                    "id": post.id,
                    "post_id": post.post_id,
                    "author": post.author_nickname or post.qq_number,
                    "qq_number": post.qq_number,
                    "content": post.content or "",
                    "images": post.images or [],
                    "time": post.post_time.isoformat() if post.post_time else "",
                })

            xhs_query = select(XHSNote).order_by(desc(XHSNote.post_time)).limit(limit)
            if since:
                xhs_query = xhs_query.where(XHSNote.post_time > since)
            xhs_result = await db.execute(xhs_query)
            for note in xhs_result.scalars().all():
                results.append({
                    "platform": "xhs",
                    "id": note.id,
                    "note_id": note.note_id,
                    "author": note.author_nickname or note.xhs_uid,
                    "content": f"{note.title or ''}\n{note.content or ''}".strip(),
                    "images": note.images or [],
                    "time": note.post_time.isoformat() if note.post_time else "",
                })

        results.sort(key=lambda x: x["time"], reverse=True)
        return results

    def _extract_keywords(self, query: str) -> list[str]:
        """简单关键词提取"""
        stop_words = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
                      "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看",
                      "好", "自己", "这", "他", "她", "吗", "什么", "最近", "有没有", "怎么"}
        words = []
        current = ""
        for char in query:
            if char.isalnum() or '\u4e00' <= char <= '\u9fff':
                current += char
            else:
                if current and current not in stop_words and len(current) >= 2:
                    words.append(current)
                current = ""
        if current and current not in stop_words and len(current) >= 2:
            words.append(current)
        return words[:5]

    def _calc_relevance(self, query: str, content: str) -> float:
        """简单相关度计算"""
        if not content:
            return 0.0
        query_lower = query.lower()
        content_lower = content.lower()
        score = 0.0
        keywords = self._extract_keywords(query)
        for kw in keywords:
            if kw.lower() in content_lower:
                score += 1.0
        if query_lower in content_lower:
            score += 2.0
        return score

    async def get_all_records_for_context(self) -> tuple[str, list[dict], dict]:
        """获取全量动态记录，格式化为紧凑的AI上下文文本。

        Returns:
            (context_text, sources, stats)
            - context_text: 格式化后的全量上下文字符串
            - sources: 来源索引列表 [{type, id, snippet}]
            - stats: 统计信息 {qq_count, xhs_count, total, truncated}
        """
        from sqlalchemy.orm import selectinload

        all_records: list[dict] = []
        sources: list[dict] = []

        async with async_session() as db:
            # ===== 获取全部QQ动态（含评论） =====
            qq_result = await db.execute(
                select(QQPost)
                .options(selectinload(QQPost.comments))
                .order_by(asc(QQPost.post_time))
            )
            for post in qq_result.scalars().all():
                content = (post.content or "").strip()
                if not content and not post.images and not post.video_url:
                    continue  # 跳过完全空的记录

                record = {
                    "platform": "qq",
                    "id": post.id,
                    "author": post.author_nickname or post.qq_number or "未知",
                    "qq_number": post.qq_number or "",
                    "time": post.post_time.strftime("%Y-%m-%d %H:%M") if post.post_time else "未知时间",
                    "content": content,
                    "images": post.images or [],
                    "has_video": bool(post.video_url or post.local_video_path),
                    "video_url": post.local_video_path or post.video_url or "",
                    "likes": post.like_count or 0,
                    "comment_count": post.comment_count or 0,
                    "forward": post.forward_content or "",
                    "location": post.location or "",
                    "device": post.device_info or "",
                    "comments": [],
                }

                # 添加评论（最多5条）
                if post.comments:
                    for c in sorted(post.comments, key=lambda x: x.comment_time or x.created_at)[:5]:
                        record["comments"].append({
                            "author": c.author_nickname or c.author_qq or "匿名",
                            "content": (c.content or "")[:200],
                            "time": c.comment_time.strftime("%m-%d %H:%M") if c.comment_time else "",
                        })

                all_records.append(record)
                sources.append({
                    "type": "qq",
                    "id": post.id,
                    "snippet": content[:80],
                })

            # ===== 获取全部小红书笔记（含评论） =====
            xhs_result = await db.execute(
                select(XHSNote)
                .options(selectinload(XHSNote.comments))
                .order_by(asc(XHSNote.post_time))
            )
            for note in xhs_result.scalars().all():
                title = (note.title or "").strip()
                content = (note.content or "").strip()
                full_text = f"{title}\n{content}".strip() if title else content
                if not full_text and not note.images and not note.video_url:
                    continue

                record = {
                    "platform": "xhs",
                    "id": note.id,
                    "author": note.author_nickname or note.xhs_uid or "未知",
                    "time": note.post_time.strftime("%Y-%m-%d %H:%M") if note.post_time else "未知时间",
                    "title": title,
                    "content": full_text,
                    "note_type": note.note_type or "normal",
                    "images": note.images or [],
                    "has_video": bool(note.video_url or note.local_video_path),
                    "video_url": note.local_video_path or note.video_url or "",
                    "likes": note.like_count or 0,
                    "collects": note.collect_count or 0,
                    "comment_count": note.comment_count or 0,
                    "shares": note.share_count or 0,
                    "tags": note.tags[:5] if note.tags else [],
                    "ip_location": note.ip_location or "",
                    "location": note.location or "",
                    "comments": [],
                }

                # 添加评论（最多5条）
                if note.comments:
                    for c in sorted(note.comments, key=lambda x: x.comment_time or x.created_at)[:5]:
                        record["comments"].append({
                            "author": c.author_nickname or "匿名",
                            "content": (c.content or "")[:200],
                            "time": c.comment_time.strftime("%m-%d %H:%M") if c.comment_time else "",
                            "is_author": bool(c.is_author),
                        })

                all_records.append(record)
                sources.append({
                    "type": "xhs",
                    "id": note.id,
                    "snippet": full_text[:80],
                })

        # 按时间排序（从旧到新）
        all_records.sort(key=lambda r: r.get("time", ""))

        # 格式化为紧凑文本
        context_lines = []
        total_chars = 0
        truncated = 0
        qq_count = sum(1 for r in all_records if r["platform"] == "qq")
        xhs_count = sum(1 for r in all_records if r["platform"] == "xhs")

        for r in all_records:
            line = self._format_record_compact(r)
            if total_chars + len(line) > MAX_CONTEXT_CHARS:
                truncated += 1
                continue
            context_lines.append(line)
            total_chars += len(line)

        context_text = "\n".join(context_lines)

        stats = {
            "qq_count": qq_count,
            "xhs_count": xhs_count,
            "total": qq_count + xhs_count,
            "included": len(context_lines),
            "truncated": truncated,
            "chars": total_chars,
        }

        if truncated:
            logger.warning(f"AI上下文超出预算，截断了{truncated}条记录（已含{len(context_lines)}条，{total_chars}字符）")
        else:
            logger.info(f"AI全量上下文: QQ={qq_count}条, XHS={xhs_count}条, 共{total_chars}字符")

        return context_text, sources, stats

    def _format_record_compact(self, r: dict) -> str:
        """将单条记录格式化为紧凑的文本行"""
        platform_tag = "QQ" if r["platform"] == "qq" else "小红书"
        meta_parts = []

        # 基础元数据
        images = r.get("images") or []
        if images:
            meta_parts.append(f"{len(images)}图")
        if r.get("has_video"):
            meta_parts.append("有视频")
        if r.get("likes"):
            meta_parts.append(f"👍{r['likes']}")
        if r.get("collects"):
            meta_parts.append(f"⭐{r['collects']}")
        if r.get("comment_count"):
            meta_parts.append(f"💬{r['comment_count']}")
        if r.get("shares"):
            meta_parts.append(f"🔗{r['shares']}")
        if r.get("location"):
            meta_parts.append(f"📍{r['location']}")
        if r.get("ip_location"):
            meta_parts.append(f"IP:{r['ip_location']}")
        if r.get("device"):
            meta_parts.append(f"设备:{r['device']}")
        if r.get("tags"):
            meta_parts.append(f"标签:{','.join(r['tags'][:3])}")

        meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""

        # 构建主体
        parts = [f"[{platform_tag}] {r['author']} | {r['time']}{meta_str}"]

        # 标题（小红书）
        if r.get("title"):
            parts.append(f"  标题: {r['title']}")

        # 内容
        content = r.get("content", "")
        if content:
            # 去掉标题部分避免重复
            if r.get("title") and content.startswith(r["title"]):
                content = content[len(r["title"]):].strip()
            if content:
                parts.append(f"  {content}")

        # 转发内容
        if r.get("forward"):
            parts.append(f"  [转发] {r['forward']}")

        # 图片URL列表
        if images:
            for i, img_url in enumerate(images):
                # 优先使用本地路径
                display_url = img_url
                if isinstance(img_url, dict):
                    display_url = img_url.get("local_path") or img_url.get("url") or str(img_url)
                parts.append(f"  [图片{i+1}] {display_url}")

        # 视频URL
        if r.get("video_url"):
            parts.append(f"  [视频] {r['video_url']}")

        # 评论
        if r.get("comments"):
            for c in r["comments"]:
                author_tag = "(作者)" if c.get("is_author") else ""
                parts.append(f"  ↳ {c['author']}{author_tag}: {c['content']}")

        return "\n".join(parts) + "\n---"

    async def get_summary_context(self) -> tuple[str, list[dict], dict]:
        """使用预生成的动态总结作为上下文（适用于数据量过大时）。

        Returns:
            (context_text, sources, stats)
        """
        from legacy.models.post_summary import PostSummary

        all_summaries: list[dict] = []
        sources: list[dict] = []

        async with async_session() as db:
            result = await db.execute(
                select(PostSummary).order_by(asc(PostSummary.post_time))
            )
            for s in result.scalars().all():
                all_summaries.append({
                    "platform": s.platform,
                    "post_db_id": s.post_db_id,
                    "author": s.author or "未知",
                    "time": s.post_time.strftime("%Y-%m-%d %H:%M") if s.post_time else "未知时间",
                    "summary": s.summary,
                    "images_count": s.images_count or 0,
                    "has_video": s.has_video or False,
                })
                sources.append({
                    "type": s.platform,
                    "id": s.post_db_id,
                    "snippet": s.summary[:80] if s.summary else "",
                })

            # 统计未总结的记录数
            from sqlalchemy import func as sa_func
            qq_total = (await db.execute(select(sa_func.count()).select_from(QQPost))).scalar() or 0
            xhs_total = (await db.execute(select(sa_func.count()).select_from(XHSNote))).scalar() or 0
            summarized_count = len(all_summaries)

        # 格式化为紧凑文本
        context_lines = []
        total_chars = 0
        truncated = 0

        for s in all_summaries:
            platform_tag = "QQ" if s["platform"] == "qq" else "小红书"
            meta = []
            if s["images_count"]:
                meta.append(f"{s['images_count']}图")
            if s["has_video"]:
                meta.append("有视频")
            meta_str = f" ({', '.join(meta)})" if meta else ""
            line = f"[{platform_tag}] {s['author']} | {s['time']}{meta_str}\n  {s['summary']}\n---"

            if total_chars + len(line) > MAX_CONTEXT_CHARS:
                truncated += 1
                continue
            context_lines.append(line)
            total_chars += len(line)

        context_text = "\n".join(context_lines)
        qq_summarized = sum(1 for s in all_summaries if s["platform"] == "qq")
        xhs_summarized = sum(1 for s in all_summaries if s["platform"] == "xhs")

        stats = {
            "mode": "summary",
            "qq_count": qq_total,
            "xhs_count": xhs_total,
            "total": qq_total + xhs_total,
            "summarized": summarized_count,
            "unsummarized": (qq_total + xhs_total) - summarized_count,
            "included": len(context_lines),
            "truncated": truncated,
            "chars": total_chars,
        }

        logger.info(f"AI总结上下文: 已总结{summarized_count}条, 总数{qq_total + xhs_total}条, {total_chars}字符")
        return context_text, sources, stats

    async def get_search_context(self, query: str, top_k: int = 30) -> tuple[str, list[dict], dict]:
        """基于问题的智能搜索上下文（AI自动匹配相关动态）。

        Returns:
            (context_text, sources, stats)
        """
        results = await self.search(query, top_k=top_k)

        context_lines = []
        sources = []
        total_chars = 0

        for r in results:
            record = {
                "platform": r["platform"],
                "author": r.get("author", "未知"),
                "time": r.get("time", "未知时间"),
                "content": r.get("content", ""),
                "images": r.get("images", []),
                "has_video": False,
                "video_url": "",
                "likes": 0,
                "collects": 0,
                "comment_count": 0,
                "shares": 0,
                "tags": [],
                "ip_location": "",
                "location": "",
                "device": "",
                "forward": "",
                "comments": [],
            }
            line = self._format_record_compact(record)
            if total_chars + len(line) > MAX_CONTEXT_CHARS:
                break
            context_lines.append(line)
            total_chars += len(line)
            sources.append({
                "type": r["platform"],
                "id": r["id"],
                "snippet": r.get("content", "")[:80],
            })

        context_text = "\n".join(context_lines)
        stats = {
            "mode": "search",
            "query": query,
            "matched": len(results),
            "included": len(context_lines),
            "chars": total_chars,
        }

        logger.info(f"AI搜索上下文: 查询'{query}', 匹配{len(results)}条, 使用{len(context_lines)}条, {total_chars}字符")
        return context_text, sources, stats

    async def get_context_auto(self, query: str) -> tuple[str, list[dict], dict]:
        """自动选择最佳上下文策略。

        优先使用全量上下文；若超出限制，检查是否有足够的总结数据，
        有则使用总结模式，否则使用搜索模式。
        """
        # 先尝试全量
        context_text, sources, stats = await self.get_all_records_for_context()

        if stats["truncated"] == 0:
            # 全量未截断，直接使用
            stats["mode"] = "full"
            return context_text, sources, stats

        # 全量被截断，尝试总结模式
        from legacy.models.post_summary import PostSummary
        async with async_session() as db:
            from sqlalchemy import func as sa_func
            summary_count = (await db.execute(
                select(sa_func.count()).select_from(PostSummary)
            )).scalar() or 0

        if summary_count >= stats["total"] * 0.7:
            # 超过70%的记录已有总结，使用总结模式
            logger.info(f"全量上下文被截断({stats['truncated']}条)，切换到总结模式(已总结{summary_count}条)")
            return await self.get_summary_context()
        else:
            # 总结数据不够，使用搜索模式
            logger.info(f"全量上下文被截断({stats['truncated']}条)，总结不足({summary_count}条)，切换到搜索模式")
            return await self.get_search_context(query)


rag_service = RAGService()
