"""Check an outpatient service list against TARDOC's tariff rules, and say what is wrong."""

from .engine import validate
from .model import Finding, Invoice, Patient, Service
from .rules import RuleSet

__all__ = ["Finding", "Invoice", "Patient", "RuleSet", "Service", "validate"]
__version__ = "0.1.0"
