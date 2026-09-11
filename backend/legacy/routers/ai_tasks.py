from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from legacy.database import get_db
from legacy.models.ai_task import AITask, TaskLog
from legacy.schemas import AITaskCreate
from legacy.security import require_admin_http

router = APIRouter(dependencies=[Depends(require_admin_http)])


def _validate_cron_expr(cron_expr: str) -> str:
    from apscheduler.triggers.cron import CronTrigger

    normalized = cron_expr.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Cron 表达式不能为空。")

    try:
        CronTrigger.from_crontab(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"无效的 Cron 表达式: {exc}") from exc

    return normalized


async def _validate_notify_target(db: AsyncSession, target_qq: str) -> str:
    normalized_target = target_qq or "admin"
    if normalized_target == "admin":
        return normalized_target

    from legacy.models.account import Account
    from legacy.models.system_config import SystemConfig

    check_result = await db.execute(
        select(Account).where(
            Account.platform == "qq",
            Account.is_target == 1,
            Account.account_id == normalized_target,
        )
    )
    if not check_result.scalar_one_or_none():
        return normalized_target

    allow_result = await db.execute(
        select(SystemConfig).where(SystemConfig.key == "allow_send_to_monitored")
    )
    allow_cfg = allow_result.scalar_one_or_none()
    if allow_cfg and allow_cfg.value == "true":
        return normalized_target

    raise HTTPException(
        status_code=400,
        detail=f"安全拦截：QQ {normalized_target} 是被监控账号，禁止将其设为通知目标。",
    )


async def _resolve_task_config(req: AITaskCreate, db: AsyncSession) -> dict[str, object]:
    from legacy.services.ai_service import ai_service

    task_analysis = await ai_service.parse_task_type_and_interval(req.description)
    task_type = req.task_type or task_analysis.get("task_type", "recurring")

    cron_expr = req.cron_expr.strip() if req.cron_expr else None
    interval_minutes = req.interval_minutes
    ai_suggested = task_analysis.get("suggested_interval_minutes")
    if not isinstance(ai_suggested, int):
        ai_suggested = None

    if cron_expr:
        cron_expr = _validate_cron_expr(cron_expr)
        interval_minutes = None
    elif interval_minutes is not None:
        if interval_minutes < 5:
            raise HTTPException(status_code=400, detail="固定间隔至少为 5 分钟。")
        cron_expr = None
    else:
        cron_expr = task_analysis.get("cron_expr")
        if cron_expr:
            cron_expr = _validate_cron_expr(cron_expr)
            interval_minutes = None
        else:
            interval_minutes = ai_suggested or 120

    target_qq = await _validate_notify_target(db, req.target_qq or "admin")

    return {
        "task_type": task_type,
        "cron_expr": cron_expr,
        "interval_minutes": interval_minutes,
        "ai_suggested_interval": ai_suggested,
        "target_qq": target_qq,
    }


async def _resolve_updated_task_config(req: AITaskCreate, task: AITask, db: AsyncSession) -> dict[str, object]:
    from legacy.services.ai_service import ai_service

    cron_expr = req.cron_expr.strip() if req.cron_expr else None
    interval_minutes = req.interval_minutes
    ai_suggested = task.ai_suggested_interval

    if cron_expr:
        cron_expr = _validate_cron_expr(cron_expr)
        interval_minutes = None
    elif interval_minutes is not None:
        if interval_minutes < 5:
            raise HTTPException(status_code=400, detail="固定间隔至少为 5 分钟。")
        cron_expr = None
    else:
        task_analysis = await ai_service.parse_task_type_and_interval(req.description)
        inferred_cron = task_analysis.get("cron_expr")
        suggested_interval = task_analysis.get("suggested_interval_minutes")
        if isinstance(suggested_interval, int):
            ai_suggested = suggested_interval
        if inferred_cron:
            cron_expr = _validate_cron_expr(inferred_cron)
            interval_minutes = None
        else:
            cron_expr = None
            interval_minutes = ai_suggested or 120

    target_qq = await _validate_notify_target(db, req.target_qq or "admin")

    return {
        "task_type": req.task_type or task.task_type or "recurring",
        "cron_expr": cron_expr,
        "interval_minutes": interval_minutes,
        "ai_suggested_interval": ai_suggested,
        "target_qq": target_qq,
    }


@router.get("")
async def get_tasks(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AITask).order_by(AITask.created_at.desc()))
    tasks = result.scalars().all()
    return [
        {
            "id": t.id, "name": t.name, "description": t.description,
            "target_qq": t.target_qq or "admin",
            "task_type": t.task_type or "recurring",
            "cron_expr": t.cron_expr,
            "interval_minutes": t.interval_minutes,
            "ai_suggested_interval": t.ai_suggested_interval,
            "message_format": t.message_format,
            "is_active": t.is_active,
            "last_run": t.last_run.isoformat() if t.last_run else None,
            "last_triggered": t.last_triggered.isoformat() if t.last_triggered else None,
            "last_checked_until": t.last_checked_until.isoformat() if t.last_checked_until else None,
            "run_count": t.run_count, "trigger_count": t.trigger_count,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tasks
    ]


@router.post("")
async def create_task(req: AITaskCreate, db: AsyncSession = Depends(get_db)):
    resolved_config = await _resolve_task_config(req, db)

    task = AITask(
        name=req.name,
        description=req.description,
        target_qq=resolved_config["target_qq"],
        task_type=resolved_config["task_type"],
        cron_expr=resolved_config["cron_expr"],
        interval_minutes=resolved_config["interval_minutes"],
        ai_suggested_interval=resolved_config["ai_suggested_interval"],
        message_format=req.message_format,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # 注册到调度器
    from legacy.services.task_scheduler import scheduler_service
    scheduler_service.register_task(task)

    task_type = str(resolved_config["task_type"])
    type_label = {"temporal": "时效性/事件驱动", "recurring": "定期检查", "oneoff": "一次性"}.get(task_type, task_type)
    return {
        "id": task.id,
        "task_type": task_type,
        "cron_expr": resolved_config["cron_expr"],
        "interval_minutes": resolved_config["interval_minutes"],
        "ai_suggested_interval": resolved_config["ai_suggested_interval"],
        "message": f"任务创建成功（类型: {type_label}）"
    }


@router.put("/{task_id}")
async def update_task(task_id: int, req: AITaskCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AITask).where(AITask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    resolved_config = await _resolve_updated_task_config(req, task, db)

    task.name = req.name
    task.description = req.description
    task.target_qq = resolved_config["target_qq"]
    task.task_type = resolved_config["task_type"]
    task.cron_expr = resolved_config["cron_expr"]
    task.interval_minutes = resolved_config["interval_minutes"]
    task.ai_suggested_interval = resolved_config["ai_suggested_interval"]
    task.message_format = req.message_format
    if task.task_type != "temporal":
        task.last_checked_until = None
    await db.commit()

    from legacy.services.task_scheduler import scheduler_service
    if task.is_active:
        scheduler_service.update_task(task)
    else:
        scheduler_service.remove_task(task_id)

    return {"message": "更新成功"}


@router.delete("/{task_id}")
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AITask).where(AITask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    from legacy.services.task_scheduler import scheduler_service
    scheduler_service.remove_task(task_id)

    await db.delete(task)
    await db.commit()
    return {"message": "删除成功"}


@router.post("/{task_id}/toggle")
async def toggle_task(task_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AITask).where(AITask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    task.is_active = not task.is_active
    await db.commit()

    from legacy.services.task_scheduler import scheduler_service
    if task.is_active:
        scheduler_service.register_task(task)
    else:
        scheduler_service.remove_task(task_id)

    return {"is_active": task.is_active}


@router.post("/{task_id}/run-now")
async def run_task_now(task_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AITask).where(AITask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    from legacy.services.task_scheduler import scheduler_service
    await scheduler_service.execute_task(task_id)
    return {"message": "任务已触发执行"}


@router.get("/{task_id}/logs")
async def get_task_logs(task_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(TaskLog).where(TaskLog.task_id == task_id).order_by(TaskLog.run_at.desc()).limit(50)
    )
    logs = result.scalars().all()
    return [
        {
            "id": l.id, "task_id": l.task_id,
            "run_at": l.run_at.isoformat() if l.run_at else None,
            "triggered": l.triggered, "ai_reason": l.ai_reason,
            "message_sent": l.message_sent, "error": l.error,
        }
        for l in logs
    ]
