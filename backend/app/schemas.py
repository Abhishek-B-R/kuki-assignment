"""Structured outputs the model must produce. These double as the JSON schemas sent to Gemini."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocType = Literal[
    "electricity_bill",
    "water_bill",
    "consumption_workbook",
    "waste_record",
    "policy",
    "other",
]

Category = Literal[
    "period",
    "consumption",
    "generation",
    "meter_reading",
    "financial",
    "quantity",
    "rate",
    "demand",
    "date",
    "identifier",
    "party",
    "policy_scope",
    "policy_commitment",
    "other",
]

# Metrics the financial-year engine knows how to total. Anything else uses null.
Metric = Literal[
    "electricity_consumption",
    "renewable_generation",
    "water_consumption",
    "waste_quantity",
    "amount_payable",
    "cost",
    "savings",
    "emissions_avoided",
]


class ExtractedFact(BaseModel):
    id: str = Field(description="Short unique id within this document: f1, f2, ...")
    key: str = Field(description="snake_case name, e.g. units_consumed, net_payable_amount, billing_period_start")
    label: str = Field(description="The label exactly as the document prints it")
    category: Category
    metric: Metric | None = Field(description="Set only for consumption/quantity/money values that could be totalled")
    dimension: str | None = Field(description="What slice the value covers: total, grid, solar, wind, renewable, TOD 18-22h, ETP sludge, ...")
    primary: bool = Field(
        description="True for the document's own recorded values for its own reporting period. False for history/trend "
        "charts, previous-period figures, averages, targets, and derived summary statistics."
    )
    value_text: str = Field(description="The value exactly as written, including symbols and Cr/Dr markers")
    value_num: float | None = Field(description="Numeric value; credits (Cr) and deductions negative; null if not numeric")
    unit: str | None
    period_start: str | None = Field(description="YYYY-MM-DD start of the period this value covers, if any")
    period_end: str | None = Field(description="YYYY-MM-DD end of the period (inclusive)")
    page: int | None = Field(description="1-based PDF page the value is printed on")
    sheet: str | None = Field(description="Spreadsheet sheet name")
    cell_range: str | None = Field(description="Spreadsheet cell or range, e.g. B16 or A3:K3")
    quote: str | None = Field(description="Shortest verbatim snippet from the text layer that shows the value")
    notes: str | None = Field(description="Assumptions, e.g. 'year inferred from sheet title'")


class Relation(BaseModel):
    description: str = Field(description="What the document implies, e.g. 'line items add up to the current bill amount'")
    operation: Literal["sum", "difference", "product"] = Field(
        description="sum: target = (sum of operands) x factor. difference: target = (operands[0] - operands[1]) x factor, "
        "e.g. (present reading - previous reading) x multiplying factor = consumption. product: target = operands "
        "multiplied together x factor, e.g. quantity x rate = amount."
    )
    target: str = Field(description="Fact id (f7) or cell reference (Sheet!B16) holding the stated result")
    operands: list[str] = Field(description="Fact ids or cell references/ranges. Prefix '-' to subtract in a sum.")
    factor: float | None = Field(description="Multiplier applied to the result, e.g. a meter multiplying factor")


class Period(BaseModel):
    label: str
    start: str | None
    end: str | None


class Anomaly(BaseModel):
    description: str
    page: int | None
    sheet: str | None
    cell_range: str | None
    quote: str | None


class Extraction(BaseModel):
    doc_type: DocType
    title: str
    organisation: str | None = Field(description="The customer, facility or company the document is about")
    issuer: str | None = Field(description="Who issued the document (utility, authority, company)")
    summary: str
    reporting_periods: list[Period]
    facts: list[ExtractedFact]
    relations: list[Relation]
    gaps: list[str] = Field(description="Fields the document leaves blank or information you would expect but is missing")
    anomalies: list[Anomaly] = Field(description="Internal contradictions or suspicious values, e.g. dates out of order")


# ---- question answering ----------------------------------------------------------------

AnswerStatus = Literal["answered", "partial", "not_found", "conflicting"]


class Citation(BaseModel):
    document_id: str
    page: int | None
    sheet: str | None
    cell_range: str | None
    fact_id: str | None = Field(description="Id of the extracted fact used, if any")
    quote: str = Field(description="Verbatim text from the document (or the cell value)")


class KeyValue(BaseModel):
    label: str
    value: str
    unit: str | None
    origin: Literal["reported", "calculated"]
    citation_indexes: list[int] = Field(description="0-based indexes into `citations` supporting this value")


class Calculation(BaseModel):
    description: str
    expression: str = Field(description="Plain arithmetic using only numbers, + - * / and parentheses, e.g. (14821 - 12500)")
    result: float
    unit: str | None


class Answer(BaseModel):
    status: AnswerStatus
    answer: str = Field(description="Direct answer in a few sentences (markdown allowed)")
    key_values: list[KeyValue]
    calculations: list[Calculation]
    citations: list[Citation]
    gaps: list[str] = Field(description="Missing information that limits the answer")
    inconsistencies: list[str] = Field(description="Conflicts found between or within documents")
    assumptions: list[str]


# ---- v2: spreadsheet series --------------------------------------------------------------


class SheetSeries(BaseModel):
    key: str = Field(description="snake_case name of the column, e.g. grid_supply_kwh")
    header: str = Field(description="The column header exactly as printed, e.g. 'Grid Supply (kWh)'")
    sheet: str
    label_column: str = Field(description="Column letter holding each row's period label (month name, 'Jan - Mar', 'Q1', a date)")
    value_column: str = Field(description="Column letter holding the values")
    first_row: int = Field(description="First data row (1-based), excluding headers")
    last_row: int = Field(description="Last data row, excluding total rows")
    metric: Metric
    dimension: str = Field(description="total, grid, solar, wind, renewable, ...")
    unit: str | None
    year: int = Field(description="Calendar year of the first data row's period")
    quarter_basis: Literal["calendar", "indian_fy"] | None = Field(
        default=None,
        description="How to read labels like 'Q1': calendar quarters (Q1 = Jan-Mar) or Indian FY quarters (Q1 = Apr-Jun);"
        " null when the labels are not quarters (read as calendar)",
    )
    year_note: str | None = Field(description="Where the year comes from, e.g. 'sheet title says 2026'")


class SheetExtraction(Extraction):
    series: list[SheetSeries] = Field(
        description="Every per-period column (monthly, quarterly, ...) of consumption, generation or money values"
    )
