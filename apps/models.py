# -*- encoding: utf-8 -*-
from datetime import datetime
from email.policy import default
from apps import db
from sqlalchemy.exc import SQLAlchemyError
from apps.exceptions.exception import InvalidUsage
import datetime as dt
from sqlalchemy.orm import relationship
from enum import Enum

class STATUS_TYPE(Enum):
    OPEN = 'OPEN'
    CLOSED = 'CLOSED'

class SeverityLevel(Enum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class Case(db.Model):
    __tablename__ = 'cases'

    id = db.Column(db.Integer, primary_key=True)
    case_title = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    severity = db.Column(db.Enum(SeverityLevel), nullable=False, default=SeverityLevel.MEDIUM)
    status = db.Column(db.Enum(STATUS_TYPE), default=STATUS_TYPE.OPEN, nullable=False)

    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    date_modified = db.Column(db.DateTime, default=db.func.current_timestamp(),
                              onupdate=db.func.current_timestamp())

    def __repr__(self):
        return f"{self.case_title} / Severity: {self.severity}"
    
    def __init__(self, **kwargs):
        super(Case, self).__init__(**kwargs)

    def __repr__(self):
        return f"{self.name} / ${self.price}"

    @classmethod
    def find_by_id(cls, _id: int) -> "Case":
        return cls.query.filter_by(id=_id).first() 

    @classmethod
    def get_list(cls):
        return cls.query.all()

    def save(self) -> None:
        try:
            db.session.add(self)
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            db.session.close()
            error = str(e.__dict__['orig'])
            raise InvalidUsage(error, 422)

    def delete(self) -> None:
        try:
            db.session.delete(self)
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            db.session.close()
            error = str(e.__dict__['orig'])
            raise InvalidUsage(error, 422)
        return
