import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from database import Base


def gen_uuid():
    return str(uuid.uuid4())


class Folder(Base):
    __tablename__ = "folders"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    parent_id = Column(String, ForeignKey("folders.id", ondelete="CASCADE"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    subfolders = relationship("Folder", backref="parent", remote_side=[id], cascade="all, delete")
    sessions = relationship("Session", back_populates="folder")


class Session(Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True, default=gen_uuid)
    session_code = Column(String, nullable=True)  # human-readable join code e.g. CS101-QUIZ
    title = Column(String, nullable=False)
    description = Column(Text, default="")
    admin_token = Column(String, nullable=False)
    status = Column(String, default="waiting")  # waiting/active/paused/ended
    per_question_time = Column(Integer, default=30)
    time_limit = Column(Integer, default=0)   # 0 = untimed
    randomize_questions = Column(Boolean, default=True)
    randomize_options = Column(Boolean, default=True)
    max_strikes = Column(Integer, default=3)
    pacing_mode = Column(String, default="auto")
    class_name = Column(String, nullable=True)   # e.g. "B.Tech 23", "MCA 24"
    category = Column(String, nullable=True)     # e.g. "Unit Test", "Mid Sem"
    folder_id = Column(String, ForeignKey("folders.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    checkpoint_data = Column(Text, nullable=True)   # JSON blob

    students = relationship("Student", back_populates="session", cascade="all, delete")
    questions = relationship("Question", back_populates="session", cascade="all, delete")
    submissions = relationship("Submission", back_populates="session", cascade="all, delete")
    violations = relationship("Violation", back_populates="session", cascade="all, delete")
    folder = relationship("Folder", back_populates="sessions")


class Student(Base):
    __tablename__ = "students"
    id = Column(String, primary_key=True, default=gen_uuid)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    name = Column(String, nullable=False)
    roll_number = Column(String, nullable=False)
    status = Column(String, default="joined")  # joined/active/disconnected/locked/submitted
    score = Column(Float, default=0.0)
    correct_count = Column(Integer, default=0)
    strike_count = Column(Integer, default=0)
    joined_at = Column(DateTime, default=datetime.utcnow)
    submitted_at = Column(DateTime, nullable=True)

    session = relationship("Session", back_populates="students")
    submissions = relationship("Submission", back_populates="student")
    violations = relationship("Violation", back_populates="student")


class Question(Base):
    __tablename__ = "questions"
    id = Column(String, primary_key=True, default=gen_uuid)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    question_type = Column(String, default="mcq")
    options = Column(JSON, nullable=False)       # list of option strings
    correct_index = Column(Integer, nullable=False)  # index into options
    points = Column(Float, default=10.0)
    time_limit = Column(Integer, default=30)

    session = relationship("Session", back_populates="questions")
    submissions = relationship("Submission", back_populates="question")


class Submission(Base):
    __tablename__ = "submissions"
    id = Column(String, primary_key=True, default=gen_uuid)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    student_id = Column(String, ForeignKey("students.id"), nullable=False)
    question_id = Column(String, ForeignKey("questions.id"), nullable=False)
    selected_option = Column(Integer, nullable=True)   # index in shuffled order shown to student
    actual_option = Column(Integer, nullable=True)     # index in original options
    is_correct = Column(Boolean, default=False)
    score_awarded = Column(Float, default=0.0)
    time_taken = Column(Float, default=0.0)
    submitted_at = Column(DateTime, default=datetime.utcnow)
    is_cached = Column(Boolean, default=False)

    session = relationship("Session", back_populates="submissions")
    student = relationship("Student", back_populates="submissions")
    question = relationship("Question", back_populates="submissions")


class Violation(Base):
    __tablename__ = "violations"
    id = Column(String, primary_key=True, default=gen_uuid)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    student_id = Column(String, ForeignKey("students.id"), nullable=False)
    violation_type = Column(String, nullable=False)  # tab_switch/focus_loss/fullscreen_exit
    description = Column(Text, default="")
    strike_number = Column(Integer, default=1)
    occurred_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="violations")
    student = relationship("Student", back_populates="violations")


class Result(Base):
    __tablename__ = "results"
    id = Column(String, primary_key=True, default=gen_uuid)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    student_id = Column(String, ForeignKey("students.id"), nullable=False)
    final_score = Column(Float, default=0.0)
    correct_count = Column(Integer, default=0)
    total_questions = Column(Integer, default=0)
    rank = Column(Integer, default=0)
    violations_count = Column(Integer, default=0)
    completion_time = Column(Float, default=0.0)
    computed_at = Column(DateTime, default=datetime.utcnow)
