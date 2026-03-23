from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi import Body
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, BOOLEAN
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from typing import List, Optional
import pydantic

# 数据库配置
SQLALCHEMY_DATABASE_URL = "sqlite:///./data/db/sqlite.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=True, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

app = FastAPI(title="Automation Test Platform")

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

class Module(Base):
    __tablename__ = "modules"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    parent_id = Column(Integer, ForeignKey("modules.id"), nullable=True)
    name = Column(String, nullable=False)
    sort_order = Column(Integer, default=0)

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


# --- 核心接口示例 ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
def get_home():
    html_file = open("client/index.html", 'r').read()
    return html_file

@app.get("/api/projects")
def get_projects(db: Session = Depends(get_db)):
    return db.query(Project).order_by(Project.sort_order).all()

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

@app.get("/api/modules/{project_id}")
def get_modules(project_id: int, parent_id: Optional[int] = None, db: Session = Depends(get_db)):
    modules = db.query(Module).filter(Module.project_id == project_id, Module.parent_id == parent_id).all()
    if modules:
        return modules
    raise HTTPException(status_code=404, detail="模块不存在")

@app.post("/api/modules")
def create_module(module: ModuleCreate, db: Session = Depends(get_db)):
    db_module = Module(**module.dict())
    if  db_module.project_id is None or db_module.name is None:
        raise HTTPException(status_code=400, detail="名称不能为空")
    db.add(db_module)
    db.commit()
    db.refresh(db_module)
    return db_module

# 初始化数据库
Base.metadata.create_all(bind=engine)
# --- 核心业务接口 ---
@app.post("/api/test_case")
def create_cases_content(case: TestCaseCreate, db: Session = Depends(get_db)):
    # 获取子模块
    db_test_cases = TestCase(**case.dict())
    db.add(db_test_cases)
    db.commit()
    db.refresh(db_test_cases)
    return db_test_cases


@app.get("/api/content/{project_id}")
def get_folder_content(project_id: int, parent_id: int = None, db: Session = Depends(get_db)):
    """
    获取当前层级的子模块和测试用例，按 sort_order 排序
    """
    # 获取子模块
    modules = db.query(Module).filter(
        Module.project_id == project_id,
        Module.parent_id == parent_id
    ).all()

    # 获取当前层级的测试用例 (如果是顶级模块 parent_id 为 None，则 module_id 匹配 project 的逻辑需按需调整)
    # 这里假设只有进入了 module 才能看到 test_cases
    cases = []
    if parent_id:
        cases = db.query(TestCase).filter(TestCase.module_id == parent_id).all()

    # 合并并标记类型以便前端渲染
    result = []
    for m in modules:
        result.append({"id": m.id, "name": m.name, "type": "module", "sort_order": m.sort_order})
    for c in cases:
        result.append({
            "id": c.id, "name": c.name, "type": "case",
            "method": c.method, "path": c.path, "sort_order": c.sort_order
        })

    # 按 sort_order 排序
    return sorted(result, key=lambda x: x['sort_order'])

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
    print(db)
    db.commit()
    return {"status": "success"}




if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=54351)