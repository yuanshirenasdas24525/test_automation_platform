import pydantic
from typing import Optional




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