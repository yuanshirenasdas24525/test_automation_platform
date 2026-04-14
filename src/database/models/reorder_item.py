from pydantic import BaseModel
from typing import List

class ReorderItem(BaseModel):
    id: int
    type: str
    new_order: int

class ReorderRequest(BaseModel):
    items: List[ReorderItem]