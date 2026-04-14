import os
from typing import List, Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException, Body, UploadFile, File, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from src.utils.reload_config import config_center
from src.database.models import (Project, Module, TestCase,
                                 ProjectCreate, ModuleCreate, TestCaseCreate,
                                 RunTestRequest, ConfigStore, ConfigUpdateItem,
                                 TestReport, ResponseModel)
from src.database.db import DB
from sqlalchemy import func

def get_db():
    db = DB()
    try:
        yield db
        db.commit()
    except:
        db.rollback()
        raise
    finally:
        db.close()

if not os.path.exists("data/reports"):
    os.makedirs("data/reports")

app = FastAPI(title="Automation Test Platform")
app.mount("/static", StaticFiles(directory="client"), name="static")


# 跨域配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- 核心接口示例 ---
@app.get("/", response_class=HTMLResponse)
def get_home():
    # 确保路径正确
    with open("client/index.html", 'r', encoding='utf-8') as f:
        return f.read()


# 修改后端代码
@app.get("/api/projects/list")
def get_projects(type: str = None, db: DB = Depends(get_db)):
    # 1. 查询项目基础列表
    query = db.session.query(Project)

    if type:
        query = query.filter(Project.type.ilike(type))

    projects = query.order_by(Project.sort_order).all()

    results = []
    for proj in projects:
        # --- 核心修改：使用方案一（跨表 Join）统计用例总数 ---
        # 逻辑：统计所有属于“该项目下的模块”的用例
        case_count = db.session.query(func.count(TestCase.id)) \
            .join(Module, TestCase.module_id == Module.id) \
            .filter(Module.project_id == proj.id) \
            .scalar()

        # 获取上次执行记录 (TestReport 直接关联了 project_id)
        last_report = db.session.query(TestReport).filter(TestReport.project_id == proj.id) \
            .order_by(TestReport.create_time.desc()).first()

        # 计算通过率
        pass_rate = 0
        if last_report and last_report.total_count > 0:
            pass_rate = round((last_report.pass_count / last_report.total_count) * 100, 1)

        # 构建返回给前端的字典
        proj_data = {
            "id": proj.id,
            "name": proj.name,
            "type": proj.type,
            "desc": proj.description,
            "case_count": case_count or 0,
            "pass_rate": pass_rate,
            "last_status": last_report.status if last_report else "unknown",
            "last_run_time": last_report.create_time.strftime("%Y-%m-%d %H:%M") if last_report else "从未执行"
        }
        results.append(proj_data)

    return {
        "status": "success",
        "data": results
    }

@app.post("/api/projects")
def create_project(project: ProjectCreate, db: DB = Depends(get_db)):
    db_project = Project(**project.dict())
    if db_project.name is None or db_project.type is None:
        raise HTTPException(status_code=400, detail="名称和类型不能为空")
    if len(db_project.name) > 10 or len(db_project.description) > 50:
        raise HTTPException(status_code=400, detail="名称或描述超过了上限")
    db.session.add(db_project)
    db.session.commit()
    db.session.refresh(db_project)
    return {"status": "success", "data": db_project}

@app.put("/api/projects/{project_id}")
def update_project(project_id: int, project_data: ProjectCreate, db: DB = Depends(get_db)):
    db_project = db.session.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 更新字段
    for key, value in project_data.dict().items():
        setattr(db_project, key, value)

    db.session.commit()
    db.session.refresh(db_project)
    return {"status": "success", "data": db_project}

@app.get("/api/projects/{project_id}")
def get_project_info(project_id: int, db: DB = Depends(get_db)):
    project = db.session.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"status": "success",
            "data": {
                "id": project.id,
                "name": project.name,
                "type": project.type,
                "description": project.description
            }
    }


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, db: DB = Depends(get_db)):
    db_project = db.session.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 注意：如果设置了级联删除(cascade)，删除项目会自动删除关联的模块和用例
    db.session.delete(db_project)
    db.session.commit()
    return {"status": "success","message": "项目已删除"}

@app.post("/api/modules")
def create_module(module: ModuleCreate, db: DB = Depends(get_db)):
    db_module = Module(**module.dict())
    if  db_module.project_id is None or db_module.name is None:
        raise HTTPException(status_code=400, detail="名称不能为空")
    db.session.add(db_module)
    db.session.commit()
    db.session.refresh(db_module)
    return {"status": "success", "data": db_module}


# --- 获取单个模块详情 (用于编辑回填) ---
@app.get("/api/modules/{module_id}")
def get_module_detail(module_id: int, db: DB = Depends(get_db)):
    module = db.session.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="模块不存在")
    return {"status": "success", "data": module}


# --- 编辑模块 ---
@app.put("/api/modules/{module_id}")
def update_module(module_id: int, name: str = Body(..., embed=True), db: DB = Depends(get_db)):
    db_module = db.session.query(Module).filter(Module.id == module_id).first()
    if not db_module:
        raise HTTPException(status_code=404, detail="模块不存在")

    db_module.name = name
    db.commit()
    return {"status": "success", "message": "修改成功"}


# --- 删除模块 ---
@app.delete("/api/modules/{module_id}")
def delete_module(module_id: int, db: DB = Depends(get_db)):
    db_module = db.session.query(Module).filter(Module.id == module_id).first()
    if not db_module:
        raise HTTPException(status_code=404, detail="模块不存在")

    # 逻辑建议：这里可以递归删除子模块，或者简单删除当前模块
    db.session.delete(db_module)
    db.session.commit()
    return {"status": "success", "message": "模块已删除"}

# --- 核心业务接口 ---
@app.post("/api/test_cases")
def create_cases_content(case: TestCaseCreate, db: DB = Depends(get_db)):
    # 获取子模块
    db_test_cases = TestCase(**case.dict())
    db.session.add(db_test_cases)
    db.session.commit()
    db.session.refresh(db_test_cases)
    return {"status": "success", "data": db_test_cases}

@app.put("/api/test_cases/{case_id}")
def edit_case_content(case_id: int, case: TestCaseCreate, db: DB = Depends(get_db)):
    db.session.query(TestCase).filter(TestCase.module_id == case.dict().get("module_id"),
                              TestCase.id == case_id).update(case.dict())
    db.session.commit()
    return {"status": "success", "message": "修改成功"}

@app.get("/api/content/{project_id}")
def get_folder_content(project_id: int, parent_id: Optional[int] = None, db: DB = Depends(get_db)):
    """
    获取当前层级的子模块和测试用例
    """
    if parent_id == 0:  # 假设你的 ID 从 1 开始，0 可以作为根目录标识
        parent_id = None

    modules = db.session.query(Module).filter(
        Module.project_id == project_id,
        Module.parent_id == parent_id
    ).all()

    # 获取当前层级的测试用例 (如果是顶级模块 parent_id 为 None，则 module_id 匹配 project 的逻辑需按需调整)
    # 这里假设只有进入了 module 才能看到 test_cases
    cases = []
    if parent_id is not None:
        cases = db.session.query(TestCase).filter(TestCase.module_id == parent_id).all()
    else:
        pass

    # 3. 合并数据
    result = []
    for m in modules:
        result.append({
            "id": m.id,
            "name": m.name,
            "type": "module",
            "sort_order": m.sort_order,
            "parent_id": m.parent_id
        })

    for c in cases:
        result.append({
            "id": c.id,
            "module_id": c.module_id,
            "type": "case",
            "name": c.name,
            "description": c.description,
            "skip":c.skip,
            "method": c.method,
            "path": c.path,
            "headers":c.headers,
            "data_type": c.data_type,
            "params": c.params,
            "file_path": c.file_path,
            "extract_data": c.extract_data,
            "sql_query":c.sql_query,
            "assertion": c.assertion,
            "wait_time": c.wait_time,
            "sort_order": c.sort_order
        })

    # 4. 排序返回
    return sorted(result, key=lambda x: x.get('sort_order', 0))

@app.delete("/api/test_cases/{content_id}")
def delete_case_content(content_id: int, db: DB = Depends(get_db)):
    """
    删除用例或模块
    """
    db.session.query(TestCase).filter(TestCase.id == content_id).delete()
    db.session.commit()
    return {"status": "success", "message": "删除成功"}

@app.post("/api/projects/{project_id}/import_cases")
async def import_test_cases(
        project_id: int,
        module_id: int,  # 导入到哪个子模块下
        file: UploadFile = File(...),
        db: DB = Depends(get_db)
):
    # 1. 读取上传的文件
    import pandas as pd
    import io
    contents = await file.read()
    df = pd.read_excel(io.BytesIO(contents))

    # 2. 字段映射（根据你的模板列名）
    # 模板列名: case_module, case_submodule, case_name, case_title, skip, method, path...
    import_count = 0
    try:
        for index, row in df.iterrows():
            # 创建用例对象
            new_case = TestCase(
                module_id=module_id,
                name=str(row['case_title']) if pd.notna(row['case_title']) else "未命名",
                description=row['case_name'],
                method=row['method'].upper(),
                path=str(row['path']).strip(),
                data_type=row['parametric_type'] if pd.notna(row['parametric_type']) else "application/json",
                headers=str(row['header']) if pd.notna(row['header']) else None,
                params=str(row['data']) if pd.notna(row['data']) else None,
                extract_data=str(row['extra']) if pd.notna(row['extra']) else None,
                assertion=str(row['expect']) if pd.notna(row['expect']) else None,
                sql_query=str(row['sql']) if pd.notna(row['sql']) else None,
                skip=True if row['skip'] == 'y' or row['skip'] == 'Y' else False,
                wait_time=int(row['wait']) if pd.notna(row['wait']) else 0,
                sort_order=int(index)
            )
            db.session.add(new_case)
            import_count += 1

        db.session.commit()
        return {"status": "success", "message": f"成功导入 {import_count} 条用例"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"文件解析失败: {str(e)}")

@app.post("/api/test_cases")
def create_test_case(case_data: TestCaseCreate, db: DB = Depends(get_db)):
    # 1. 如果指定了插入位置
    if case_data.sort_order is not None:
        # 将当前模块下，顺序号大于等于新用例的所有用例后移一位
        db.session.query(TestCase).filter(
            TestCase.module_id == case_data.module_id,
            TestCase.sort_order >= case_data.sort_order
        ).update({TestCase.sort_order: TestCase.sort_order + 1})
    else:
        # 如果没指定，默认放到最后
        max_order = db.session.query(func.max(TestCase.sort_order)).filter(
            TestCase.module_id == case_data.module_id
        ).scalar() or 0
        case_data.sort_order = max_order + 1

    # 2. 创建新用例
    new_case = TestCase(**case_data.dict())
    db.session.add(new_case)
    db.session.commit()
    return {"status": "success", "data": new_case}


@app.post("/api/run_test")
async def run_test(req: RunTestRequest, db: DB = Depends(get_db)):
    from src.utils.read_test_cases import get_cases_from_db
    import uuid

    # 1. 生成唯一任务 ID
    now = datetime.now()
    task_id = f"{now.strftime('%Y%m%d%H%M%S')}_{str(uuid.uuid4())[:8]}"
    if not req.category:
        return {"status": "error","message":"缺少项目类型 api/web/app"}
    try:

        params = {
            "project": req.project,
            "module": req.module,
            "category": req.category,
            "case": req.case
        }

        cases_to_run = get_cases_from_db(params, db.sql)
        caseNumber = len(cases_to_run)


        if not cases_to_run:
            return {"status": "error", "message": "未找到可执行的用例"}

        # 状态设为 running，这样前端可以显示“进行中”
        new_report = TestReport(
            project_id=req.project,
            category=req.category if req.category else "ERROR",
            status="running",
            start_time=now,
            executor="System",  # 或者从 session 获取用户名
            total_count=len(cases_to_run)
        )
        db.session.add(new_report)
        db.session.commit()
        db.session.refresh(new_report)
        report_id = new_report.id

        from tasks import run_test_task
        run_test_task.delay(task_id, report_id, cases_to_run, req.category)

        return {
            "status": "success",
            "report_id": report_id,
            "task_id": task_id,
            "case_number": caseNumber,
            "message": "测试已在后台启动"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.patch("/api/reorder")
def reorder_items(data: list = Body(...), db: DB = Depends(get_db)):
    """
    批量更新排序。前端发送格式: [{"id": 1, "type": "module", "new_order": 0}, ...]
    """
    for item in data:
        if item['type'] == 'module':
            db.session.query(Module).filter(Module.id == item['id']).update({"sort_order": item['new_order']})
        else:
            db.session.query(TestCase).filter(TestCase.id == item['id']).update({"sort_order": item['new_order']})
    db.session.commit()
    return {"status": "success"}


@app.get("/api/config/all")
async def get_all_configs(category: Optional[str] = Query(None), db: DB = Depends(get_db)):

    if category:
        # 根据类型筛选
        sql = "SELECT * FROM config_store WHERE category = :category ORDER BY config_group"
        params = {"category": category.lower()}
    else:
        # 返回所有数据
        sql = "SELECT * FROM config_store ORDER BY category, config_group"
        params = {}
    data = db.sql.query(sql, params)
    return {"status": "success", "data": data}


@app.post("/api/config/save")
async def save_configs(configs: ConfigUpdateItem, db: DB = Depends(get_db)):
    group = configs.config_group
    key = configs.config_key
    val = configs.config_value
    category = configs.category
    if not category:
        return {"status": "error", "message": "category 不能为空"}
    if not group or not key :
        return {"status": "error", "message": "Group 和 Key 不能为空"}
    try:
        db_item = db.session.query(ConfigStore).filter(
            ConfigStore.config_group == group,
            ConfigStore.config_key == key,
            ConfigStore.category == category
        ).first()

        if db_item:
            # 2. 如果存在，执行修改
            db_item.config_value = val
            db_item.update_time = datetime.now()
        else:
            # 3. 如果不存在，执行新增
            new_item = ConfigStore(
                config_group=group,
                config_key=key,
                config_value=val,
                category=category
            )
            db.session.add(new_item)
        db.session.commit()
        config_center.reload(db.sql)  # 触发热更新
        return {"status": "success", "message": "保存成功"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}


@app.post("/api/config/add")
def add_config(item: ConfigUpdateItem, db: DB = Depends(get_db)):
    group = item.config_group
    if not group:
        return {"status": "error", "message": "category 不能为空"}

    sql = """
          INSERT INTO config_store (config_group, config_key, config_value, category)
          VALUES (:g, :k, :v, :c) \
          """
    params = {
        "g": item.config_group,
        "k": item.config_key,
        "v": item.config_value,
        "c": item.category or 'api'
    }

    db.sql.execute(sql, params)

    return {"status": "success"}


# --- 删除 (Delete) ---
@app.delete("/api/config/delete/{config_id}")
async def delete_config(config_id: int, db: DB = Depends(get_db)):
    db.sql.execute("DELETE FROM config_store WHERE id = :id", {"id": config_id})
    config_center.reload(db.sql)
    return {"status": "success"}



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=54351)