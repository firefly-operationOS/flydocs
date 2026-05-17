"""Shared test data for the example scripts.

Defines an invoice ``DocSpec`` and rule set that the other examples
reuse, so each script stays focused on one capability instead of
re-defining the schema.
"""

from __future__ import annotations

from flydocs_sdk import (
    DocSpec,
    DocType,
    FieldGroup,
    FieldItem,
    FieldSpec,
    FieldType,
    RuleFieldParent,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
    StandardFormat,
    StandardValidatorSpec,
    StandardValidatorType,
)

INVOICE_DOC_SPEC = DocSpec(
    doc_type=DocType(document_type="invoice", description="Vendor invoice", country="ES"),
    field_groups=[
        FieldGroup.of(
            "header",
            FieldSpec(field_name="invoice_number", field_type=FieldType.STRING, required=True),
            FieldSpec(
                field_name="invoice_date",
                field_type=FieldType.STRING,
                format=StandardFormat.DATE,
                required=True,
            ),
            FieldSpec(
                field_name="supplier_vat",
                field_type=FieldType.STRING,
                required=True,
                standard_validators=[
                    StandardValidatorSpec(type=StandardValidatorType.VAT_ID, params={"country": "ES"}),
                ],
            ),
        ),
        FieldGroup.of(
            "totals",
            FieldSpec(field_name="subtotal", field_type=FieldType.NUMBER, required=True, minimum=0.0),
            FieldSpec(field_name="tax_amount", field_type=FieldType.NUMBER, required=True, minimum=0.0),
            FieldSpec(field_name="total_amount", field_type=FieldType.NUMBER, required=True, minimum=0.0),
            FieldSpec(field_name="currency", field_type=FieldType.STRING, required=True),
        ),
        FieldGroup.of(
            "line_items_block",
            FieldSpec(
                field_name="line_items",
                field_type=FieldType.ARRAY,
                items=[
                    FieldItem(field_name="description", field_type=FieldType.STRING),
                    FieldItem(field_name="quantity", field_type=FieldType.NUMBER),
                    FieldItem(field_name="unit_price", field_type=FieldType.NUMBER),
                    FieldItem(field_name="line_total", field_type=FieldType.NUMBER),
                ],
            ),
        ),
    ],
)

INVOICE_RULES = [
    RuleSpec(
        id="totals_consistent",
        predicate="subtotal + tax_amount equals total_amount within 0.01",
        parents=[
            RuleFieldParent(
                document_type="invoice",
                field_names=["subtotal", "tax_amount", "total_amount"],
            )
        ],
    ),
    RuleSpec(
        id="vat_id_valid",
        predicate="The supplier_vat field passes the VAT_ID validator",
        parents=[RuleValidatorParent(document_type="invoice", validator_name="vat_id")],
    ),
    RuleSpec(
        id="invoice_acceptable",
        predicate="totals_consistent AND vat_id_valid",
        parents=[
            RuleRuleParent(rule_id="totals_consistent"),
            RuleRuleParent(rule_id="vat_id_valid"),
        ],
    ),
]
