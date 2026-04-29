from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.ai_task import AITask, TaskLog
from app.schemas import AITaskCreate, AITaskOut, TaskLogOut

router = APIRouter()


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
    from app.services.ai_service import ai_service

    # === AI分析任务类型和频率 ===
    task_analysis = await ai_service.parse_task_type_and_interval(req.description)
    task_type = req.task_type if req.task_type != "recurring" else task_analysis.get("task_type", "recurring")

    # === 解析cron / interval ===
    cron_expr = req.cron_expr
    interval_minutes = req.interval_minutes
    ai_suggested = task_analysis.get("suggested_interval_minutes")

    if not cron_expr and not interval_minutes:
        cron_expr = task_analysis.get("cron_expr")
        if not cron_expr:
            interval_minutes = ai_suggested or 120

    # === 通知目标 ===
    target_qq = req.target_qq or "admin"

    # === 安全校验：不允许将目标设为被监控账号 ===
    if target_qq != "admin":
        from app.models.account import Account
        from app.models.system_config import SystemConfig
        check_result = await db.execute(
            select(Account).where(
                Account.platform == "qq",
                Account.is_target == 1,
                Account.account_id == target_qq
            )
        )
        if check_result.scalar_one_or_none():
            # 检查是否允许
            allow_result = await db.execute(
                select(SystemConfig).where(SystemConfig.key == "allow_send_to_monitored")
            )
            allow_cfg = allow_result.scalar_one_or_none()
            if not (allow_cfg and allow_cfg.value == "true"):
                raise HTTPException(
                    status_code=400,
                    detail=f"安全拦截：QQ {target_qq} 是被监控账号，禁止将其设为通知目标。"
                )

    task = AITask(
        name=req.name,
        description=req.description,
        target_qq=target_qq,
        task_type=task_type,
        cron_expr=cron_expr,
        interval_minutes=interval_minutes,
        ai_suggested_interval=ai_suggested,
        message_format=req.message_format,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # 注册到调度器
    from app.services.task_scheduler import scheduler_service
    scheduler_service.register_task(task)

    type_label = {"temporal": "时效性/事件驱动", "recurring": "定期检查", "oneoff": "一次性"}.get(task_type, task_type)
    return {
        "id": task.id,
        "task_type": task_type,
        "cron_expr": cron_expr,
        "interval_minutes": interval_minutes,
        "ai_suggested_interval": ai_suggested,
        "message": f"任务创建成功（类型: {type_label}）"
    }


@router.put("/{task_id}")
async def update_task(task_id: int, req: AITaskCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AITask).where(AITask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    task.name = req.name
    task.description = req.description
    task.target_qq = req.target_qq
    task.cron_expr = req.cron_expr or task.cron_expr
    task.interval_minutes = req.interval_minutes
    task.message_format = req.message_format
    await db.commit()

    from app.services.task_scheduler import scheduler_service
    scheduler_service.update_task(task)

    return {"message": "更新成功"}


@router.delete("/{task_id}")
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AITask).where(AITask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    from app.services.task_scheduler import scheduler_service
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

    from app.services.task_scheduler import scheduler_service
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

    from app.services.task_scheduler import scheduler_service
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
