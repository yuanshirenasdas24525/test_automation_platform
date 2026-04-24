from pydantic import BaseModel

class ResponseModel(BaseModel):
    status: str
    message: str