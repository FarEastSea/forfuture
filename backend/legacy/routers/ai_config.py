from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from legacy.database import get_db
from legacy.models.ai_config import AIConfig
from legacy.schemas import AIConfigCreate, AIConfigUpdate
from legacy.security import require_admin_http

router = APIRouter(dependencies=[Depends(require_admin_http)])


@router.get("")
async def get_configs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AIConfig).order_by(AIConfig.created_at.desc()))
    configs = result.scalars().all()
    return [
        {
            "id": c.id, "name": c.name, "api_base": c.api_base,
            "api_key": f"{c.api_key[:4]}...{c.api_key[-4:]}" if c.api_key and len(c.api_key) > 8 else (c.api_key[:2] + "****" if c.api_key else ""),
            "model": c.model, "embed_model": c.embed_model,
            "max_tokens": c.max_tokens, "temperature": c.temperature,
            "is_active": c.is_active,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in configs
    ]


@router.post("")
async def create_config(req: AIConfigCreate, db: AsyncSession = Depends(get_db)):
    config = AIConfig(
        name=req.name, api_base=req.api_base, api_key=req.api_key,
        model=req.model, embed_model=req.embed_model,
        max_tokens=req.max_tokens, temperature=req.temperature,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return {"id": config.id, "message": "配置创建成功"}


@router.put("/{config_id}")
async def update_config(config_id: int, req: AIConfigUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AIConfig).where(AIConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    config.name = req.name
    config.api_base = req.api_base
    if req.api_key:
        config.api_key = req.api_key
    config.model = req.model
    config.embed_model = req.embed_model
    config.max_tokens = req.max_tokens
    config.temperature = req.temperature
    await db.commit()
    return {"message": "更新成功"}


@router.delete("/{config_id}")
async def delete_config(config_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AIConfig).where(AIConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    await db.delete(config)
    await db.commit()
    return {"message": "删除成功"}


@router.post("/{config_id}/activate")
async def activate_config(config_id: int, db: AsyncSession = Depends(get_db)):
    # 先取消所有激活
    all_configs = (await db.execute(select(AIConfig))).scalars().all()
    for c in all_configs:
        c.is_active = False

    result = await db.execute(select(AIConfig).where(AIConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    config.is_active = True
    await db.commit()
    return {"message": f"已激活配置: {config.name}"}


@router.post("/test")
async def test_config(req: AIConfigCreate):
    from legacy.services.ai_service import ai_service
    try:
        result = await ai_service.test_connection(req.api_base, req.api_key, req.model)
        return {"success": True, "message": result}
    except Exception as e:
        return {"success": False, "message": str(e)}
