from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class QuestionCreate(BaseModel):
    text: str
    options: List[str] = Field(min_length=2, max_length=6)
    correct_index: int
    points: float = 10.0
    time_limit: int = 30
    question_type: str = "mcq"


class SessionCreate(BaseModel):
    title: str
    session_code: Optional[str] = None   # e.g. "CS101-QUIZ" — students use this to join
    description: str = ""
    admin_token: str
    per_question_time: int = 30
    time_limit: int = 0
    randomize_questions: bool = True
    randomize_options: bool = True
    max_strikes: int = 3
    pacing_mode: str = "auto"
    questions: List[QuestionCreate] = []


class SessionInfo(BaseModel):
    id: str
    title: str
    description: str
    status: str
    per_question_time: int
    time_limit: int
    pacing_mode: str = "auto"
    created_at: datetime
    student_count: int = 0
    question_count: int = 0

    class Config:
        from_attributes = True


class StudentJoin(BaseModel):
    session_id: str
    name: str
    roll_number: str


class QuestionAddBatch(BaseModel):
    admin_token: str
    questions: List[QuestionCreate]
