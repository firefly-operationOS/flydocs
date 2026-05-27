"""Shared test data for the example scripts.

Defines a v1 invoice :class:`DocumentTypeSpec` and rule set that the
other examples reuse, so each script stays focused on one capability
instead of re-defining the schema. Add this directory to ``PYTHONPATH``
when running examples that import from it.
"""

from __future__ import annotations

from flydocs_sdk import (
    DocumentTypeSpec,
    Field,
    FieldGroup,
    FieldType,
    RuleFieldParent,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
    StandardFormat,
    ValidatorSpec,
    ValidatorType,
)

INVOICE_DOCUMENT_TYPE = DocumentTypeSpec(
    id="invoice",
    description="Vendor invoice",
    country="ES",
    field_groups=[
        FieldGroup(
            name="header",
            fields=[
                Field(name="invoice_number", type=FieldType.STRING, required=True),
                Field(
                    name="invoice_date",
                    type=FieldType.STRING,
                    format=StandardFormat.DATE,
                    required=True,
                ),
                Field(
                    name="supplier_vat",
                    type=FieldType.STRING,
                    required=True,
                    validators=[
                        ValidatorSpec(name=ValidatorType.VAT_ID, params={"country": "ES"}),
                    ],
                ),
            ],
        ),
        FieldGroup(
            name="totals",
            fields=[
                Field(name="subtotal", type=FieldType.NUMBER, required=True, minimum=0.0),
                Field(name="tax_amount", type=FieldType.NUMBER, required=True, minimum=0.0),
                Field(name="total_amount", type=FieldType.NUMBER, required=True, minimum=0.0),
                Field(
                    name="currency",
                    type=FieldType.STRING,
                    required=True,
                    validators=[ValidatorSpec(name=ValidatorType.CURRENCY_CODE)],
                ),
            ],
        ),
        FieldGroup(
            name="line_items_block",
            fields=[
                Field(
                    name="line_items",
                    type=FieldType.ARRAY,
                    items=Field(
                        name="row",
                        type=FieldType.OBJECT,
                        fields=[
                            Field(name="description", type=FieldType.STRING),
                            Field(name="quantity", type=FieldType.NUMBER, minimum=0),
                            Field(name="unit_price", type=FieldType.NUMBER, minimum=0),
                            Field(name="line_total", type=FieldType.NUMBER, minimum=0),
                        ],
                    ),
                ),
            ],
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
                fields=["subtotal", "tax_amount", "total_amount"],
            )
        ],
    ),
    RuleSpec(
        id="vat_id_valid",
        predicate="The supplier_vat field passes the vat_id validator",
        parents=[RuleValidatorParent(document_type="invoice", validator="vat_id")],
    ),
    RuleSpec(
        id="invoice_acceptable",
        predicate="totals_consistent AND vat_id_valid",
        parents=[
            RuleRuleParent(rule="totals_consistent"),
            RuleRuleParent(rule="vat_id_valid"),
        ],
    ),
]
