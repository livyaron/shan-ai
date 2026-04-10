from sqlalchemy import Column, Integer, BigInteger, String, Text, DateTime, Float, Boolean, ForeignKey, Enum, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from datetime import datetime
import enum

Base = declarative_base()

class RoleEnum(str, enum.Enum):
    PROJECT_MANAGER = "project_manager"
    DEPARTMENT_MANAGER = "department_manager"
    DEPUTY_DIVISION_MANAGER = "deputy_division_manager"
    DIVISION_MANAGER = "division_manager"

class DecisionTypeEnum(str, enum.Enum):
    INFO = "info"
    NORMAL = "normal"
    CRITICAL = "critical"
    UNCERTAIN = "uncertain"

class DecisionStatusEnum(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"

class DistributionTypeEnum(str, enum.Enum):
    INFO = "info"
    EXECUTION = "execution"
    APPROVAL = "approval"

class RaciRoleEnum(str, enum.Enum):
    RESPONSIBLE = "R"
    ACCOUNTABLE = "A"
    CONSULTED = "C"
    INFORMED = "I"

class DistributionStatusEnum(str, enum.Enum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    APPROVED = "approved"
    REJECTED = "rejected"
    DONE = "done"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=True)
    username = Column(String(255), unique=True, index=True)
    password_hash = Column(String(255), nullable=False, default="$2b$12$KIXxPfIPJ0hwi0pYjZlVBe82rJmDreXxB0E8hSXIWkIV9O8Y3bPha")  # default: bcrypt("1234")
    role = Column(Enum(RoleEnum), nullable=True, default=None)
    email = Column(String(255), nullable=True)
    registration_code = Column(String(10), unique=True, nullable=True, index=True)
    profile_token = Column(String(32), unique=True, nullable=True, index=True)
    is_admin = Column(Boolean, default=False, nullable=False)
    job_title = Column(String(255), nullable=True)
    responsibilities = Column(Text, nullable=True)   # תחומי אחריות — used for AI RACI assignment
    hierarchy_level = Column(Integer, nullable=True)
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    manager = relationship("User", remote_side="User.id", foreign_keys="User.manager_id", uselist=False)
    messages = relationship("Message", back_populates="user")
    decisions = relationship("Decision", back_populates="submitter", foreign_keys="Decision.submitter_id")

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    content = Column(Text)
    telegram_message_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", back_populates="messages")

class Decision(Base):
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, index=True)
    submitter_id = Column(Integer, ForeignKey("users.id"), index=True)

    type = Column(Enum(DecisionTypeEnum))
    status = Column(Enum(DecisionStatusEnum), default=DecisionStatusEnum.PENDING)

    summary = Column(Text)
    problem_description = Column(Text)
    recommended_action = Column(Text)
    requires_approval = Column(Boolean, default=False)

    assumptions = Column(Text)  # JSON string of assumptions list
    risks = Column(Text)  # JSON string of risks list
    measurability = Column(String(50))  # MEASURABLE, PARTIAL, NOT_MEASURABLE

    feedback_score = Column(Integer, nullable=True)       # 1-5 rating
    feedback_notes = Column(Text, nullable=True)
    feedback_requested_at = Column(DateTime, nullable=True)

    embedding = Column(Vector(384), nullable=True)        # pgvector RAG

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    submitter = relationship("User", back_populates="decisions", foreign_keys=[submitter_id])
    distributions = relationship("DecisionDistribution", back_populates="decision", cascade="all, delete-orphan")
    raci_roles = relationship("DecisionRaciRole", back_populates="decision", cascade="all, delete-orphan")


class DecisionDistribution(Base):
    __tablename__ = "decision_distributions"

    id = Column(Integer, primary_key=True, index=True)
    decision_id = Column(Integer, ForeignKey("decisions.id"), index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    distribution_type = Column(Enum(DistributionTypeEnum))
    status = Column(Enum(DistributionStatusEnum), default=DistributionStatusEnum.PENDING)
    sent_at = Column(DateTime, nullable=True)
    responded_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)

    decision = relationship("Decision", back_populates="distributions")
    user = relationship("User")


class DecisionFeedback(Base):
    __tablename__ = "decision_feedbacks"

    id          = Column(Integer, primary_key=True, index=True)
    decision_id = Column(Integer, ForeignKey("decisions.id"), index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), index=True)
    score       = Column(Integer)           # 1-5
    notes       = Column(Text, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    decision = relationship("Decision")
    user     = relationship("User")


class KnowledgeFile(Base):
    __tablename__ = "knowledge_files"

    id            = Column(Integer, primary_key=True, index=True)
    original_name = Column(String(255))
    file_path     = Column(String(512))
    file_type     = Column(String(10))           # pdf | docx | xlsx
    file_size     = Column(Integer, default=0)
    uploader_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    summary       = Column(Text, nullable=True)
    chunk_count   = Column(Integer, default=0)
    status        = Column(String(20), default="processing")  # processing | ready | error
    is_master     = Column(Boolean, default=False, nullable=False, server_default="false")
    created_at    = Column(DateTime, default=datetime.utcnow)

    uploader = relationship("User")
    chunks   = relationship("KnowledgeChunk", back_populates="file", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id        = Column(Integer, primary_key=True, index=True)
    file_id   = Column(Integer, ForeignKey("knowledge_files.id"), index=True)
    chunk_idx = Column(Integer)
    content   = Column(Text)
    embedding = Column(Vector(384), nullable=True)

    file = relationship("KnowledgeFile", back_populates="chunks")


class LessonLearned(Base):
    __tablename__ = "lessons_learned"

    id            = Column(Integer, primary_key=True, index=True)
    decision_id   = Column(Integer, ForeignKey("decisions.id"), nullable=False, index=True)
    lesson_text   = Column(Text, nullable=False)
    decision_type = Column(String(20), nullable=True)   # info/normal/critical/uncertain
    tags          = Column(Text, nullable=True)          # JSON array of keywords
    embedding     = Column(Vector(384), nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

    decision = relationship("Decision")


class KnowledgeSummary(Base):
    """Per decision-type aggregated knowledge summary, regenerated after each batch extraction."""
    __tablename__ = "knowledge_summaries"

    id            = Column(Integer, primary_key=True, index=True)
    decision_type = Column(String(20), nullable=False, unique=True, index=True)
    summary_text  = Column(Text, nullable=False)
    lesson_count  = Column(Integer, default=0)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DecisionRaciRole(Base):
    __tablename__ = "decision_raci_roles"

    id             = Column(Integer, primary_key=True, index=True)
    decision_id    = Column(Integer, ForeignKey("decisions.id"), nullable=False, index=True)
    user_id        = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role           = Column(Enum(RaciRoleEnum), nullable=False)
    assigned_by_ai = Column(Boolean, default=True, nullable=False)
    created_at     = Column(DateTime, default=datetime.utcnow)

    decision = relationship("Decision", back_populates="raci_roles")
    user     = relationship("User")


class QueryLog(Base):
    """Logs every RAG query with AI response and user feedback."""
    __tablename__ = "query_logs"

    id            = Column(Integer, primary_key=True, index=True)
    question      = Column(Text, nullable=False)
    ai_response   = Column(Text, nullable=False)
    sources_used  = Column(JSON, nullable=True)       # [{"file": "name.xlsx"}, ...]
    user_feedback = Column(Integer, default=0)         # 1=up, -1=down, 0=none
    admin_note    = Column(Text, nullable=True)
    is_accurate   = Column(Boolean, nullable=True)
    analyzed      = Column(Boolean, default=False)     # True after optimization run consumed this log
    failure_type  = Column(String(20), nullable=True)  # "TERMINOLOGY" | "STRUCTURE" | None
    fix_suggestion = Column(Text, nullable=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=True)
    timestamp     = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")


class QuerySynonym(Base):
    """Learned synonyms from optimization runs, used to expand future queries."""
    __tablename__ = "query_synonyms"

    id         = Column(Integer, primary_key=True)
    original   = Column(String(255), unique=True, nullable=False, index=True)
    synonyms   = Column(JSON, nullable=False)   # list of strings
    source     = Column(String(20), default="ai")  # "ai" or "admin"
    created_at = Column(DateTime, default=datetime.utcnow)
