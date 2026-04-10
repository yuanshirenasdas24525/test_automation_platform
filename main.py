from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi import Body, UploadFile, File, APIRouter, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, BOOLEAN, func, JSON, DateTime, Float
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.ext.declarative import declarative_base
from src.utils.reload_config import config_center
from src.utils.logger import LOGGER
from src.common.context import ctx
from typing import List, Optional
import pandas as pd
import pydantic
import io
import uuid
import os

# 数据库配置
SQLALCHEMY_DATABASE_URL = "sqlite:///./data/db/sqlite.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=True, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

if not os.path.exists("data/reports"):
    os.makedirs("data/reports")

app = FastAPI(title="Automation Test Platform")
router = APIRouter(prefix="/api/config", tags=["配置管理"])
app.mount("/static", StaticFiles(directory="client"), name="static")
app.mount("/reports", StaticFiles(directory="data/reports"), name="reports")


# 跨域配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic 模型 (用于API传参) ---
class ProjectCreate(pydantic.BaseModel):
    name: str
    description: Optional[str]
    icon: Optional[str]
    type: str

# --- 更多 Pydantic 模型 ---
class ModuleCreate(pydantic.BaseModel):
    project_id: int
    parent_id: Optional[int] = None
    name: str


class ConfigUpdateItem(pydantic.BaseModel):
    config_group: str
    config_key: str
    config_value: str


class ConfigItem(pydantic.BaseModel):
    id: Optional[int] = None
    config_group: str
    config_key: str
    config_value: str
    value_type: str = "str"
    category: str
    description: Optional[str] = ""


class TestReport(Base):
    __tablename__ = "test_reports"

    id = Column(Integer, primary_key=True, index=True)

    # 关联信息
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    category = Column(String, index=True)  # api, web, mobile (方便首页按类型统计)

    # 执行信息
    scene_name = Column(String)  # 场景名称或执行任务名称
    executor = Column(String, default="Admin")  # 执行人
    start_time = Column(DateTime, server_default=func.now())
    end_time = Column(DateTime)
    duration = Column(Float)  # 耗时（秒）

    # 统计数据 (核心：用于计算通过率)
    total_count = Column(Integer, default=0)  # 总用例数
    pass_count = Column(Integer, default=0)  # 成功数
    fail_count = Column(Integer, default=0)  # 失败数
    error_count = Column(Integer, default=0)  # 错误数（程序异常）
    skip_count = Column(Integer, default=0)  # 跳过数

    # 状态与结果
    status = Column(String)  # success, fail, running
    summary = Column(String)  # 简短的错误摘要

    # 详细数据 (可选)
    allure_url = Column(String)  # 如果集成了 Allure，存储报告链接

    # 时间戳
    create_time = Column(DateTime, server_default=func.now())

class RunTestRequest(pydantic.BaseModel):
    project: int
    module: Optional[int] = None
    type: Optional[str] = None
    case: Optional[int] = None


class TestCaseCreate(pydantic.BaseModel):
    module_id: int
    name: str
    description: str
    skip: bool
    method: str
    path: str
    headers: Optional[str] = None
    data_type: Optional[str] = "application/json"
    params: Optional[str] = None
    file_path: Optional[str] = None
    extract_data: Optional[str] = None
    sql_query: Optional[str] = None
    assertion: str
    wait_time: int = None

# --- 数据库模型 (根据你的SQL需求) ---

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String)
    icon = Column(String)
    type = Column(String, nullable=False) # Mobile, API, Web
    sort_order = Column(Integer, default=0)
    modules = relationship("Module", back_populates="project", cascade="all, delete-orphan")

class Module(Base):
    __tablename__ = "modules"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    parent_id = Column(Integer, ForeignKey("modules.id"), nullable=True)
    name = Column(String, nullable=False)
    sort_order = Column(Integer, default=0)
    project = relationship("Project", back_populates="modules")
    # 模块删除时，也自动删除下的用例
    test_cases = relationship("TestCase", back_populates="module", cascade="all, delete-orphan")

class TestCase(Base):
    __tablename__ = "test_cases"
    id = Column(Integer, primary_key=True, index=True)
    module_id = Column(Integer, ForeignKey("modules.id"))
    name = Column(String, nullable=False)
    description = Column(String)
    skip = Column(BOOLEAN, nullable=False)
    method = Column(String, nullable=False)
    path = Column(String)
    headers = Column(String)
    data_type = Column(String, nullable=False)
    params = Column(String)
    file_path = Column(String)
    extract_data = Column(String)
    sql_query = Column(String)
    assertion = Column(String)
    wait_time = Column(Integer, default=0)
    sort_order = Column(Integer, default=0)
    module = relationship("Module", back_populates="test_cases")


# --- 核心接口示例 ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
def get_home():
    # 确保路径正确
    with open("client/index.html", 'r', encoding='utf-8') as f:
        return f.read()


# 修改后端代码
@app.get("/api/projects/list")
def get_projects(type: str = None, db: Session = Depends(get_db)):
    # 1. 查询项目基础列表
    query = db.query(Project)
    LOGGER.info(f"asd: {query}")
    if type:
        query = query.filter(Project.type.ilike(type))

    projects = query.order_by(Project.sort_order).all()

    results = []
    for proj in projects:
        # --- 核心修改：使用方案一（跨表 Join）统计用例总数 ---
        # 逻辑：统计所有属于“该项目下的模块”的用例
        case_count = db.query(func.count(TestCase.id)) \
            .join(Module, TestCase.module_id == Module.id) \
            .filter(Module.project_id == proj.id) \
            .scalar()

        # 获取上次执行记录 (TestReport 直接关联了 project_id)
        last_report = db.query(TestReport).filter(TestReport.project_id == proj.id) \
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
def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    db_project = Project(**project.dict())
    if db_project.name is None or db_project.type is None:
        raise HTTPException(status_code=400, detail="名称和类型不能为空")
    print(len(db_project.name))
    if len(db_project.name) > 10 or len(db_project.description) > 50:
        raise HTTPException(status_code=400, detail="名称或描述超过了上限")
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return db_project

@app.put("/api/projects/{project_id}")
def update_project(project_id: int, project_data: ProjectCreate, db: Session = Depends(get_db)):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 更新字段
    for key, value in project_data.dict().items():
        setattr(db_project, key, value)

    db.commit()
    db.refresh(db_project)
    return db_project

@app.get("/api/projects/{project_id}")
def get_project_info(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {
        "id": project.id,
        "name": project.name,
        "type": project.type,
        "description": project.description
    }


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 注意：如果设置了级联删除(cascade)，删除项目会自动删除关联的模块和用例
    db.delete(db_project)
    db.commit()
    return {"message": "项目已删除"}

@app.post("/api/modules")
def create_module(module: ModuleCreate, db: Session = Depends(get_db)):
    db_module = Module(**module.dict())
    if  db_module.project_id is None or db_module.name is None:
        raise HTTPException(status_code=400, detail="名称不能为空")
    db.add(db_module)
    db.commit()
    db.refresh(db_module)
    return db_module


# --- 获取单个模块详情 (用于编辑回填) ---
@app.get("/api/modules/{module_id}")
def get_module_detail(module_id: int, db: Session = Depends(get_db)):
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="模块不存在")
    return module


# --- 编辑模块 ---
@app.put("/api/modules/{module_id}")
def update_module(module_id: int, name: str = Body(..., embed=True), db: Session = Depends(get_db)):
    db_module = db.query(Module).filter(Module.id == module_id).first()
    if not db_module:
        raise HTTPException(status_code=404, detail="模块不存在")

    db_module.name = name
    db.commit()
    return {"message": "修改成功"}


# --- 删除模块 ---
@app.delete("/api/modules/{module_id}")
def delete_module(module_id: int, db: Session = Depends(get_db)):
    db_module = db.query(Module).filter(Module.id == module_id).first()
    if not db_module:
        raise HTTPException(status_code=404, detail="模块不存在")

    # 逻辑建议：这里可以递归删除子模块，或者简单删除当前模块
    db.delete(db_module)
    db.commit()
    return {"message": "模块已删除"}

# 初始化数据库
Base.metadata.create_all(bind=engine)
# --- 核心业务接口 ---
@app.post("/api/test_cases")
def create_cases_content(case: TestCaseCreate, db: Session = Depends(get_db)):
    # 获取子模块
    db_test_cases = TestCase(**case.dict())
    db.add(db_test_cases)
    db.commit()
    db.refresh(db_test_cases)
    return db_test_cases

@app.put("/api/test_cases/{case_id}")
def edit_case_content(case_id: int, case: TestCaseCreate, db: Session = Depends(get_db)):
    db.query(TestCase).filter(TestCase.module_id == case.dict().get("module_id"),
                              TestCase.id == case_id).update(case.dict())
    db.commit()
    return {"status": "success"}

@app.get("/api/content/{project_id}")
def get_folder_content(project_id: int, parent_id: Optional[int] = None, db: Session = Depends(get_db)):
    """
    获取当前层级的子模块和测试用例
    """
    if parent_id == 0:  # 假设你的 ID 从 1 开始，0 可以作为根目录标识
        parent_id = None

    modules = db.query(Module).filter(
        Module.project_id == project_id,
        Module.parent_id == parent_id
    ).all()

    # 获取当前层级的测试用例 (如果是顶级模块 parent_id 为 None，则 module_id 匹配 project 的逻辑需按需调整)
    # 这里假设只有进入了 module 才能看到 test_cases
    cases = []
    if parent_id is not None:
        cases = db.query(TestCase).filter(TestCase.module_id == parent_id).all()
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
def delete_case_content(content_id: int, db: Session = Depends(get_db)):
    """
    删除用例或模块
    """
    db.query(TestCase).filter(TestCase.id == content_id).delete()
    db.commit()
    return {"status": "success"}

@app.post("/api/projects/{project_id}/import_cases")
async def import_test_cases(
        project_id: int,
        module_id: int,  # 导入到哪个子模块下
        file: UploadFile = File(...),
        db: Session = Depends(get_db)
):
    # 1. 读取上传的文件
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
            db.add(new_case)
            import_count += 1

        db.commit()
        return {"message": f"成功导入 {import_count} 条用例"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"文件解析失败: {str(e)}")

@app.post("/api/test_cases")
def create_test_case(case_data: TestCaseCreate, db: Session = Depends(get_db)):
    # 1. 如果指定了插入位置
    if case_data.sort_order is not None:
        # 将当前模块下，顺序号大于等于新用例的所有用例后移一位
        db.query(TestCase).filter(
            TestCase.module_id == case_data.module_id,
            TestCase.sort_order >= case_data.sort_order
        ).update({TestCase.sort_order: TestCase.sort_order + 1})
    else:
        # 如果没指定，默认放到最后
        max_order = db.query(func.max(TestCase.sort_order)).filter(
            TestCase.module_id == case_data.module_id
        ).scalar() or 0
        case_data.sort_order = max_order + 1

    # 2. 创建新用例
    new_case = TestCase(**case_data.dict())
    db.add(new_case)
    db.commit()
    return new_case


@app.post("/api/run_test")
async def run_test(req: RunTestRequest, background_tasks: BackgroundTasks):
    from src.utils.read_test_cases import read_conf, get_cases_from_db
    con_sqlite = read_conf.get_dict("sqlite_local")
    # 1. 生成唯一任务 ID
    task_id = str(uuid.uuid4())[:8]
    try:

        params = {
            "project": req.project,
            "module": req.module,
            "category": req.type,
            "case": req.case
        }

        cases_to_run = get_cases_from_db(params, con_sqlite)


        if not cases_to_run:
            return {"status": "error", "message": "未找到可执行的用例"}

        def execute_pytest_workflow(task_id, cases):
            import pytest
            import json

            # 结果路径和报告路径
            result_path = f"data/results/{task_id}"
            report_path = f"data/reports/{task_id}"

            # A. 运行 Pytest 并生成 Allure 源数据 (JSON)
            pytest_args = [
                "-s", "-v",
                "-p", "config.pytest_config",
                "--alluredir", result_path,  # 动态指定结果目录
                "tests/service_run_executor.py::TestServiceApi::test_api_runner",
                f"--cases_data={json.dumps(cases)}"
            ]
            pytest.main(pytest_args)

            os.system(f"allure generate {result_path} -o {report_path} --clean")
            print(f"任务 {task_id} 报告生成完毕")

        background_tasks.add_task(execute_pytest_workflow, task_id, cases_to_run)

        return {
            "status": "success",
            "task_id": task_id,
            "report_url": f"/reports/{task_id}/index.html",
            "message": "测试已在后台启动，完成后可访问 report_url"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.patch("/api/reorder")
def reorder_items(data: list = Body(...), db: Session = Depends(get_db)):
    """
    批量更新排序。前端发送格式: [{"id": 1, "type": "module", "new_order": 0}, ...]
    """
    for item in data:
        if item['type'] == 'module':
            db.query(Module).filter(Module.id == item['id']).update({"sort_order": item['new_order']})
        else:
            db.query(TestCase).filter(TestCase.id == item['id']).update({"sort_order": item['new_order']})
    db.commit()
    return {"status": "success"}


@app.get("/api/config/all")
async def get_all_configs(category: Optional[str] = Query(None)):

    if category:
        # 根据类型筛选
        sql = "SELECT * FROM config_store WHERE category = :category ORDER BY config_group"
        params = {"category": category.lower()}
    else:
        # 返回所有数据
        sql = "SELECT * FROM config_store ORDER BY category, config_group"
        params = {}
    data = ctx.db.execute_query(sql, params)
    return {"status": "success", "data": data}


@app.post("/api/config/save")
async def save_configs(configs: List[ConfigItem]):
    try:
        for item in configs:
            sql = """
                  UPDATE config_store
                  SET config_value = :val, \
                      description  = :desc
                  WHERE config_group = :group \
                    AND config_key = :key \
                  """
            params = {"val": item.config_value, "desc": item.description, "group": item.config_group,
                      "key": item.config_key}
            ctx.db.execute_db(sql, params)

        config_center.reload(ctx.db)  # 触发热更新
        return {"status": "success", "message": "保存成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/config/add")
async def add_config(item: ConfigItem):
    # 1. 确保 SQL 占位符和 Params 的 Key 一一对应
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

    ctx.db.execute_db(sql, params)
    return {"status": "success"}


# --- 删除 (Delete) ---
@app.delete("/api/config/delete/{config_id}")
async def delete_config(config_id: int):
    ctx.db.execute_db("DELETE FROM config_store WHERE id = :id", {"id": config_id})
    ctx.config.reload(ctx.db)
    return {"status": "success"}


# @app.post("/api/run/project/{proj_id}")
# async def run_project(proj_id: int, category: str, background_tasks: BackgroundTasks):
#     # 使用后台任务执行测试，避免前端请求超时
#     background_tasks.add_task(execute_pytest, proj_id, category)
#     return {"status": "success", "message": "测试任务已下发"}

# def execute_pytest(proj_id, category):
#     # 动态构建 pytest 参数
#     # -q: 静默模式, --project_id: 自定义参数
#     args = [
#         f"tests/project_{proj_id}/",
#         "-q",
#         f"--project_id={proj_id}",
#         f"--category={category}"
#     ]
#     pytest.main(args)




if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=54351, workers=2)