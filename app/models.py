from sqlalchemy import Column, Integer, BigInteger, String, Text, DateTime, Float, Boolean, ForeignKey, Enum
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
    job_title = Column(String(255), nullable=True)
    hierarchy_level = Column(Integer, nullable=True)
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    manager = relationship("User", remote_side="User.id", foreign_keys="User.manager_id", uselist=False)
    messages = relationship("Message", back_populates="user")
    decisions = relationship("Decision", back_populates="submitter", foreign_keys="Decision.submitter_id")
    approvals = relationship("Decision", back_populates="approver", foreign_keys="Decision.approver_id")

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
    approver_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    type = Column(Enum(DecisionTypeEnum))
    status = Column(Enum(DecisionStatusEnum), default=DecisionStatusEnum.PENDING)

    summary = Column(Text)
    problem_description = Column(Text)
    recommended_action = Column(Text)
    confidence = Column(Float, default=0.0)
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
    approver = relationship("User", back_populates="approvals", foreign_keys=[approver_id])
    distributions = relationship("DecisionDistribution", back_populates="decision", cascade="all, delete-orphan")


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
