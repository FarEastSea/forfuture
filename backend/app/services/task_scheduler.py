import logging
import json
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


class TaskSchedulerService:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self._started = False

    def start(self):
        if not self._started:
            self.scheduler.start()
            self._started = True
            logger.info("定时任务调度器已启动")
            import asyncio
            asyncio.create_task(self._load_active_tasks())
            asyncio.create_task(self._load_auto_crawl_tasks())
            # 注册消息确认处理器
            from app.services.napcat_client import napcat_client
            napcat_client.on_message_received(self._handle_incoming_message)

    def shutdown(self):
        if self._started:
            self.scheduler.shutdown(wait=False)
            self._started = False

    async def _load_active_tasks(self):
        from app.database import async_session
        from app.models.ai_task import AITask
        from sqlalchemy import select

        try:
            async with async_session() as db:
                result = await db.execute(select(AITask).where(AITask.is_active == True))
                tasks = result.scalars().all()
                for task in tasks:
                    self.register_task(task)
                logger.info(f"已加载 {len(tasks)} 个活跃定时任务")
                # 启动未确认通知定期检查
                self.scheduler.add_job(
                    self._check_unconfirmed_wrapper,
                    trigger=IntervalTrigger(minutes=30),
                    id="check_unconfirmed",
                    replace_existing=True,
                )
        except Exception as e:
            logger.error(f"加载定时任务失败: {e}")

    async def _load_auto_crawl_tasks(self):
        """加载自动爬取定时任务"""
        from app.database import async_session
        from app.models.system_config import SystemConfig
        from sqlalchemy import select

        try:
            async with async_session() as db:
                # 读取自动爬取配置
                result = await db.execute(select(SystemConfig).where(SystemConfig.key == "auto_crawl_interval"))
                config = result.scalar_one_or_none()
                interval = int(config.value) if config and config.value else 60  # 默认60分钟

                result2 = await db.execute(select(SystemConfig).where(SystemConfig.key == "auto_crawl_enabled"))
                enabled = result2.scalar_one_or_none()
                if enabled and enabled.value == "false":
                    logger.info("自动爬取已禁用")
                    return

            self.register_auto_crawl(interval)
            logger.info(f"自动爬取已启动，间隔 {interval} 分钟")
        except Exception as e:
            # 首次启动可能没有配置，用默认值
            self.register_auto_crawl(60)
            logger.info(f"自动爬取已启动（默认60分钟间隔）: {e}")

        # 注册Cookie自动续期任务（每4小时刷新一次QQ cookies）
        self.scheduler.add_job(
            self._cookie_refresh_wrapper,
            trigger=IntervalTrigger(hours=4),
            id="cookie_refresh",
            replace_existing=True,
        )
        logger.info("Cookie自动续期任务已注册（每4小时）")

        # 注册动态总结定时任务（每周一凌晨3点更新总结）
        self.scheduler.add_job(
            self._summary_update_wrapper,
            trigger=CronTrigger(day_of_week="mon", hour=3, minute=0),
            id="weekly_summary",
            replace_existing=True,
        )
        logger.info("动态总结定时任务已注册（每周一凌晨3:00）")

    def register_auto_crawl(self, interval_minutes: int):
        job_id = "auto_crawl"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
        self.scheduler.add_job(
            self._auto_crawl_wrapper,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id=job_id,
            replace_existing=True,
        )

    async def _auto_crawl_wrapper(self):
        try:
            await self.execute_auto_crawl()
        except Exception as e:
            logger.error(f"自动爬取异常: {e}")

    async def _cookie_refresh_wrapper(self):
        """定期刷新QQ和XHS cookies，失败时自动推送二维码重新登录。
        仅在有active状态的登录账号时才执行。
        """
        try:
            from app.database import async_session
            from app.models.account import Account
            from sqlalchemy import select

            # 检查是否有active状态的QQ登录账号
            async with async_session() as db:
                qq_login = await db.execute(
                    select(Account).where(
                        Account.platform == "qq",
                        Account.is_target == 0,
                        Account.status == "active",
                    )
                )
                has_active_qq = qq_login.scalars().first() is not None

            if not has_active_qq:
                logger.debug("Cookie自动续期: 无active状态的QQ登录账号，跳过")
                return

            from app.services.qq_crawler import qq_crawler
            result = await qq_crawler.refresh_qq_cookies()
            if result:
                logger.info("QQ Cookie自动续期成功")
            else:
                logger.warning("QQ Cookie自动续期失败，尝试NapCat自动重新登录（免扫码）...")
                # 优先尝试NapCat自动登录（免扫码，通过NapCat插件获取Cookie）
                try:
                    napcat_result = await qq_crawler.login_via_napcat()
                    if napcat_result.get("success"):
                        logger.info("QQ Cookie通过NapCat自动重新登录成功（免扫码）")
                        return
                    else:
                        logger.warning(f"NapCat自动重新登录失败: {napcat_result.get('message')}")
                except Exception as e_napcat:
                    logger.warning(f"NapCat自动重新登录异常: {e_napcat}")

                # NapCat自动登录失败，降级到推送二维码扫码登录
                logger.warning("NapCat自动登录不可用，降级为推送二维码重新登录...")
                try:
                    relogin_ok = await qq_crawler.auto_relogin_via_napcat()
                    if relogin_ok:
                        logger.info("QQ Cookie自动重新登录成功（扫码）")
                        return
                    else:
                        logger.warning("QQ Cookie自动重新登录失败，需要手动扫码")
                except Exception as e_relogin:
                    logger.error(f"QQ自动重新登录异常: {e_relogin}")

                # 所有重试方法均失败，标记账号过期并通知管理员
                logger.error("QQ Cookie所有续期方法均失败，标记账号为expired")
                try:
                    async with async_session() as db:
                        acc_result = await db.execute(
                            select(Account).where(
                                Account.platform == "qq",
                                Account.is_target == 0,
                                Account.status == "active",
                            )
                        )
                        acc = acc_result.scalars().first()
                        if acc:
                            acc.status = "expired"
                            await db.commit()
                except Exception as e_db:
                    logger.error(f"标记账号过期失败: {e_db}")
                await qq_crawler._notify_cookie_expired("QQ空间", "Cookie自动续期和所有重试方法均失败，请手动登录")
        except Exception as e:
            logger.error(f"Cookie自动续期异常: {e}")

    async def _summary_update_wrapper(self):
        """定期更新动态总结（每周执行一次，包含重新总结已总结的记录）"""
        try:
            # 检查开关
            from app.database import async_session
            from app.models.system_config import SystemConfig
            from sqlalchemy import select as sa_select
            async with async_session() as db:
                result = await db.execute(
                    sa_select(SystemConfig).where(SystemConfig.key == "auto_summary_enabled")
                )
                config = result.scalar_one_or_none()
                if config and config.value == "false":
                    logger.info("每周自动总结已禁用，跳过")
                    return

            from app.services.summary_service import summary_service
            logger.info("开始执行每周动态总结更新...")
            stats = await summary_service.generate_summaries(force_all=True)
            logger.info(
                f"每周动态总结完成: 新增{stats['new']}条, 更新{stats['updated']}条, "
                f"失败{stats['failed']}条, 跳过{stats['skipped']}条"
            )
        except Exception as e:
            logger.error(f"每周动态总结失败: {e}", exc_info=True)

    async def execute_auto_crawl(self):
        """自动爬取所有已配置的QQ空间和小红书账号。
        仅在有active状态的登录账号时才尝试cookie续期/重新登录。
        """
        from app.database import async_session
        from app.models.account import Account
        from app.services.qq_crawler import qq_crawler
        from app.services.xhs_crawler import xhs_crawler
        from sqlalchemy import select

        async with async_session() as db:
            # QQ目标账号
            qq_result = await db.execute(
                select(Account).where(Account.platform == "qq", Account.is_target == 1)
            )
            qq_accounts = qq_result.scalars().all()
            qq_ids = [a.account_id for a in qq_accounts]

            # 小红书目标账号
            xhs_result = await db.execute(
                select(Account).where(Account.platform == "xhs", Account.is_target == 1)
            )
            xhs_accounts = xhs_result.scalars().all()
            xhs_ids = [a.account_id for a in xhs_accounts]

            # 检查是否有active状态的QQ登录账号
            qq_login_result = await db.execute(
                select(Account).where(
                    Account.platform == "qq",
                    Account.is_target == 0,
                    Account.status == "active",
                )
            )
            has_active_qq_login = qq_login_result.scalars().first() is not None

        if qq_ids:
            if has_active_qq_login:
                # 爬取前先刷新cookies（仅当登录账号active时）
                try:
                    refresh_ok = await qq_crawler.refresh_qq_cookies()
                    if not refresh_ok:
                        logger.warning("爬取前Cookie续期失败，尝试NapCat自动重新登录...")
                        # 优先NapCat自动登录（免扫码）
                        try:
                            napcat_result = await qq_crawler.login_via_napcat()
                            if napcat_result.get("success"):
                                logger.info("爬取前NapCat自动重新登录成功")
                            else:
                                logger.warning(f"NapCat自动登录失败: {napcat_result.get('message')}，降级为二维码...")
                                await qq_crawler.auto_relogin_via_napcat()
                        except Exception:
                            await qq_crawler.auto_relogin_via_napcat()
                except Exception as e:
                    logger.warning(f"爬取前Cookie续期失败: {e}")
            else:
                logger.debug("自动爬取: QQ登录账号未启用，跳过Cookie续期")
            logger.info(f"自动爬取QQ空间: {qq_ids}")
            await qq_crawler.start_crawl(qq_ids)

        if xhs_ids:
            logger.info(f"自动爬取小红书: {xhs_ids}")
            await xhs_crawler.start_crawl(xhs_ids)

        if not qq_ids and not xhs_ids:
            logger.info("自动爬取：暂无已配置的监控账号")

    def register_task(self, task):
        job_id = f"ai_task_{task.id}"
        # 先移除旧的
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        if task.cron_expr:
            try:
                parts = task.cron_expr.split()
                trigger = CronTrigger(
                    minute=parts[0], hour=parts[1], day=parts[2],
                    month=parts[3], day_of_week=parts[4]
                )
            except Exception:
                trigger = IntervalTrigger(hours=2)
        elif task.interval_minutes:
            trigger = IntervalTrigger(minutes=task.interval_minutes)
        else:
            trigger = IntervalTrigger(hours=2)

        self.scheduler.add_job(
            self._run_task_wrapper,
            trigger=trigger,
            id=job_id,
            args=[task.id],
            replace_existing=True,
        )
        logger.info(f"注册定时任务: {task.name} (ID={task.id})")

    def update_task(self, task):
        self.register_task(task)

    def remove_task(self, task_id: int):
        job_id = f"ai_task_{task_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    async def _run_task_wrapper(self, task_id: int):
        try:
            await self.execute_task(task_id)
        except Exception as e:
            logger.error(f"定时任务 {task_id} 执行异常: {e}")

    async def execute_task(self, task_id: int):
        from app.database import async_session
        from app.models.ai_task import AITask, TaskLog
        from app.services.rag_service import rag_service
        from app.services.ai_service import ai_service
        from app.services.napcat_client import napcat_client
        from app.models.account import Account
        from app.models.system_config import SystemConfig
        from sqlalchemy import select

        async with async_session() as db:
            result = await db.execute(select(AITask).where(AITask.id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                return

            task.last_run = datetime.now()
            task.run_count += 1

            log = TaskLog(task_id=task_id)

            try:
                # === 1. 解析通知目标 ===
                notify_target = await self._resolve_notify_target(db, task.target_qq)
                if not notify_target:
                    log.error = "未配置管理员QQ号，无法发送通知。请在设置→管理员中配置。"
                    log.ai_reason = log.error
                    db.add(log)
                    await db.commit()
                    return

                # === 2. 安全检查：禁止向被监控账号发消息 ===
                is_blocked = await self._is_send_blocked(db, notify_target)
                if is_blocked:
                    log.error = f"安全拦截：目标QQ {notify_target} 是被监控账号，已阻止发送。"
                    log.ai_reason = log.error
                    logger.warning(log.error)
                    db.add(log)
                    await db.commit()
                    return

                # === 3. 获取记录（时效性任务只获取新记录）===
                since = None
                if task.task_type == "temporal" and task.last_checked_until:
                    since = task.last_checked_until

                recent_records = await rag_service.get_recent_records(
                    limit=50, since=since
                )

                now = datetime.now()

                if not recent_records:
                    log.ai_reason = "无新记录可分析" if since else "暂无记录可分析"
                    if task.task_type == "temporal":
                        task.last_checked_until = now
                    db.add(log)
                    await db.commit()
                    return

                # === 4. AI分析 ===
                analysis = await ai_service.analyze_for_task(
                    task.description, recent_records,
                    task_type=task.task_type or "recurring",
                    since_time=since.isoformat() if since else None
                )
                log.triggered = analysis.get("triggered", False)
                log.ai_reason = analysis.get("reason", "")

                # 更新时效性任务的检查截止点
                if task.task_type == "temporal":
                    task.last_checked_until = now

                # AI建议的检查间隔 → 动态调整调度器
                suggested = analysis.get("suggested_interval_minutes")
                if suggested and isinstance(suggested, int) and 5 <= suggested <= 1440:
                    if suggested != task.ai_suggested_interval:
                        task.ai_suggested_interval = suggested
                        self._adjust_interval(task, suggested)
                        logger.info(f"任务 {task.name}: AI建议检查间隔调整为 {suggested} 分钟")

                if log.triggered:
                    task.last_triggered = datetime.now()
                    task.trigger_count += 1

                    # 构建消息
                    messages = await self._build_messages(
                        task, analysis, recent_records
                    )
                    log.message_sent = json.dumps(
                        [{"type": m["type"], "content": m.get("content", "")[:200]} for m in messages],
                        ensure_ascii=False
                    )

                    # 通过NapCat发送
                    success = await napcat_client.send_message(notify_target, messages)
                    if not success:
                        log.error = "NapCat未连接，消息发送失败"
                    else:
                        # 发送确认请求
                        await napcat_client.send_message(notify_target, [{
                            "type": "text",
                            "content": "📬 以上是AI监控推送，请回复「收到」确认已阅读。"
                        }])
                        log.confirmed = False

            except Exception as e:
                log.error = str(e)
                logger.error(f"任务 {task_id} 执行失败: {e}")

            db.add(log)
            await db.commit()

    async def _build_messages(self, task, analysis: dict, records: list[dict]) -> list[dict]:
        """根据分析结果和消息格式偏好构建消息列表"""
        messages = []
        fmt = task.message_format or "text_image"

        # 文字消息（总是包含）
        summary = analysis.get("suggested_message") or analysis.get("summary", "")
        reason = analysis.get("reason", "")
        severity = analysis.get("severity", "medium")

        severity_emoji = {"low": "📋", "medium": "⚠️", "high": "🚨"}.get(severity, "📋")
        text = f"{severity_emoji} AI监控提醒\n\n{summary}\n\n📝 分析依据：{reason}"
        messages.append({"type": "text", "content": text})

        # 引用原动态
        related_indices = analysis.get("related_records", [])
        for idx in related_indices[:3]:
            if idx < len(records):
                record = records[idx]
                ref_text = f"📌 原动态 [{record['platform'].upper()}] {record['author']} ({record['time']}):\n{record['content'][:500]}"
                messages.append({"type": "text", "content": ref_text})

                # 发送原动态图片
                if "image" in fmt and record.get("images"):
                    for img in record["images"][:3]:
                        img_path = img.get("local_path") or img.get("url", "")
                        if img_path:
                            from app.config import settings
                            if img_path.startswith("/static/"):
                                img_url = f"http://127.0.0.1:{settings.backend_port}{img_path}"
                            else:
                                img_url = img_path
                            messages.append({"type": "image", "url": img_url})

        # 如果需要渲染图片（复杂内容）
        if "image" in fmt and len(related_indices) > 1:
            html = self._render_summary_html(analysis, records, related_indices)
            messages.append({"type": "render_html", "html": html})

        return messages

    def _render_summary_html(self, analysis: dict, records: list, indices: list) -> str:
        """生成用于puppeteer渲染的HTML"""
        cards = ""
        for idx in indices[:5]:
            if idx < len(records):
                r = records[idx]
                imgs_html = ""
                for img in (r.get("images") or [])[:2]:
                    path = img.get("local_path") or img.get("url", "")
                    if path:
                        imgs_html += f'<img src="{path}" style="max-width:200px;border-radius:8px;margin:4px">'
                cards += f"""
                <div style="background:#2a2a2a;border-radius:12px;padding:16px;margin:8px 0">
                    <div style="color:#888;font-size:12px">{r['platform'].upper()} · {r['author']} · {r['time']}</div>
                    <div style="color:#eee;margin:8px 0">{r['content'][:300]}</div>
                    <div>{imgs_html}</div>
                </div>"""

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body{{background:#1a1a1a;padding:20px;font-family:sans-serif;width:400px}}
</style></head><body>
<div style="color:#fff;font-size:18px;font-weight:bold;margin-bottom:12px">📊 AI监控报告</div>
<div style="color:#aaa;margin-bottom:16px">{analysis.get('summary', '')}</div>
{cards}
</body></html>"""

    # ========== 通知目标解析 & 安全检查 ==========

    async def _resolve_notify_target(self, db, target_qq: str) -> str | None:
        """解析通知目标QQ号：'admin'或空值时从系统配置获取管理员QQ"""
        from app.models.system_config import SystemConfig
        from sqlalchemy import select

        if target_qq and target_qq != "admin":
            return target_qq
        result = await db.execute(
            select(SystemConfig).where(SystemConfig.key == "admin_qq")
        )
        config = result.scalar_one_or_none()
        if config and config.value:
            return config.value
        return None

    async def _is_send_blocked(self, db, target_qq: str) -> bool:
        """检查是否禁止向该QQ发消息（被监控账号保护）"""
        from app.models.account import Account
        from app.models.system_config import SystemConfig
        from sqlalchemy import select

        # 检查是否是被监控账号
        result = await db.execute(
            select(Account).where(
                Account.platform == "qq",
                Account.is_target == 1,
                Account.account_id == target_qq
            )
        )
        is_monitored = result.scalar_one_or_none() is not None
        if not is_monitored:
            return False

        # 检查是否允许向被监控账号发消息
        result = await db.execute(
            select(SystemConfig).where(SystemConfig.key == "allow_send_to_monitored")
        )
        config = result.scalar_one_or_none()
        allow = config and config.value == "true"
        return not allow  # 被监控 + 不允许 = blocked

    def _adjust_interval(self, task, minutes: int):
        """动态调整任务的调度间隔（AI建议）"""
        job_id = f"ai_task_{task.id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
        self.scheduler.add_job(
            self._run_task_wrapper,
            trigger=IntervalTrigger(minutes=minutes),
            id=job_id,
            args=[task.id],
            replace_existing=True,
        )
        logger.info(f"任务 {task.name} 间隔已调整为 {minutes} 分钟")

    # ========== 消息确认系统 ==========

    async def _handle_incoming_message(self, qq: str, payload: dict):
        """处理来自NapCat的消息，检查是否为确认回复"""
        content = (payload.get('content') or '').strip()
        from_qq = payload.get('from_qq', '')
        if not from_qq or not content:
            return

        # 只处理私聊
        if payload.get('msg_type') != 'private':
            return

        confirm_keywords = ['确认', '收到', '好的', 'ok', '知道了', '已阅', '收到了', '了解']
        is_confirm = any(kw in content.lower() for kw in confirm_keywords)
        if not is_confirm:
            return

        from app.database import async_session
        from app.models.ai_task import AITask, TaskLog
        from sqlalchemy import select, desc

        try:
            async with async_session() as db:
                # 找最近触发但未确认的日志
                result = await db.execute(
                    select(TaskLog).where(
                        TaskLog.triggered == True,
                        TaskLog.confirmed == False
                    ).order_by(desc(TaskLog.run_at)).limit(10)
                )
                logs = result.scalars().all()

                confirmed_count = 0
                for tlog in logs:
                    task_result = await db.execute(
                        select(AITask).where(AITask.id == tlog.task_id)
                    )
                    task = task_result.scalar_one_or_none()
                    if not task:
                        continue
                    target = await self._resolve_notify_target(db, task.target_qq)
                    if target == from_qq:
                        tlog.confirmed = True
                        tlog.confirmed_at = datetime.now()
                        confirmed_count += 1

                if confirmed_count > 0:
                    await db.commit()
                    logger.info(f"QQ {from_qq} 确认了 {confirmed_count} 条推送通知")
                    from app.services.napcat_client import napcat_client
                    await napcat_client.send_message(from_qq, [{
                        "type": "text",
                        "content": f"✅ 已确认收到 {confirmed_count} 条AI监控推送。"
                    }])
        except Exception as e:
            logger.error(f"处理确认回复失败: {e}")

    async def _check_unconfirmed_wrapper(self):
        try:
            await self._check_unconfirmed()
        except Exception as e:
            logger.error(f"检查未确认通知异常: {e}")

    async def _check_unconfirmed(self):
        """定期检查未确认的通知，重新提醒"""
        from app.database import async_session
        from app.models.ai_task import AITask, TaskLog
        from app.services.napcat_client import napcat_client
        from sqlalchemy import select, and_

        cutoff_old = datetime.now() - timedelta(hours=2)  # 超过2小时的不再提醒
        cutoff_recent = datetime.now() - timedelta(minutes=10)  # 至少10分钟前发的

        async with async_session() as db:
            result = await db.execute(
                select(TaskLog).where(
                    and_(
                        TaskLog.triggered == True,
                        TaskLog.confirmed == False,
                        TaskLog.run_at > cutoff_old,
                        TaskLog.run_at < cutoff_recent,
                    )
                )
            )
            unconfirmed = result.scalars().all()

            for tlog in unconfirmed:
                task_result = await db.execute(
                    select(AITask).where(AITask.id == tlog.task_id)
                )
                task = task_result.scalar_one_or_none()
                if not task:
                    continue
                target = await self._resolve_notify_target(db, task.target_qq)
                if target:
                    reason_preview = (tlog.ai_reason or "")[:150]
                    await napcat_client.send_message(target, [{
                        "type": "text",
                        "content": f"⏰ 你有未确认的AI监控推送（{task.name}）\n{reason_preview}\n—— 回复「收到」确认已阅读"
                    }])
                    logger.info(f"重新提醒未确认推送: 任务={task.name}, 目标={target}")


scheduler_service = TaskSchedulerService()
