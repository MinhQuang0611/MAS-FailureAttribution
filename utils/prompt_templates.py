"""
utils/prompt_templates.py

Các template ép LLM xuất dữ liệu theo format chuẩn cho từng bước pipeline.
Mỗi template nhận vào context dict và trả về chuỗi prompt hoàn chỉnh.
"""

from __future__ import annotations

from string import Template
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Step A: Intent Understanding
# ---------------------------------------------------------------------------

INTENT_CLARIFICATION_TMPL = Template("""\
You are an expert SQL analyst. Given the user question and database context below,
clarify the user's intent and decompose it into concrete subtasks.

## User Question
${question}

## Database: ${db_id}
${schema_summary}

## Instructions
- Restate the question in precise, unambiguous language.
- List any implicit constraints (time range, conditions, filters).
- Decompose into subtasks if the query is complex.
- Flag the query as ambiguous (true/false).

## Output Format (JSON only, no extra text)
{
  "clarified_question": "<precise restatement>",
  "subtasks": ["<subtask 1>", "<subtask 2>"],
  "detected_conditions": ["<condition 1>"],
  "is_ambiguous": false
}
""")


# ---------------------------------------------------------------------------
# Step B: Schema Linking
# ---------------------------------------------------------------------------

SCHEMA_FILTER_TMPL = Template("""\
You are a database schema expert. Given the question and full schema below,
select ONLY the tables and columns needed to answer the question.

## Question
${question}

## Full Schema (${db_id})
${full_schema}

## Instructions
- Only include tables and columns that are directly relevant.
- Do NOT hallucinate table/column names that don't exist.
- Provide a brief rationale for your selection.

## Output Format (JSON only, no extra text)
{
  "selected_tables": [
    {
      "table_name": "<name>",
      "columns": [{"name": "<col>", "dtype": "<type>"}]
    }
  ],
  "pruning_rationale": "<brief explanation>"
}
""")


TOKEN_ALIGNMENT_TMPL = Template("""\
Align each meaningful token in the question to the schema element it refers to.

## Question
${question}

## Filtered Schema
${filtered_schema}

## Output Format (JSON only, no extra text)
{
  "alignments": [
    {
      "token": "<word or phrase>",
      "mapped_table": "<table or null>",
      "mapped_column": "<column or null>",
      "confidence": 0.95,
      "is_hallucinated": false
    }
  ]
}
""")


# ---------------------------------------------------------------------------
# Step C: SQL Generation
# ---------------------------------------------------------------------------

QUERY_PLAN_TMPL = Template("""\
You are a SQL reasoning engine. Before writing SQL, create a step-by-step query plan.

## Question
${question}

## Filtered Schema
${filtered_schema}

## Instructions
- Describe each logical step to answer the question.
- Identify if the query needs: JOIN, GROUP BY, HAVING, subquery, aggregation.

## Output Format (JSON only, no extra text)
{
  "steps": ["<step 1>", "<step 2>"],
  "uses_aggregation": false,
  "uses_join": false,
  "uses_subquery": false,
  "uses_having": false
}
""")


SQL_SKELETON_TMPL = Template("""\
Based on the query plan below, generate a SQL SKELETON (structure without specific values).

## Question
${question}

## Query Plan
${query_plan}

## Filtered Schema
${filtered_schema}

## Instructions
- Use placeholders like _ for column names and values.
- List all SQL clauses you expect to appear.

## Output Format (JSON only, no extra text)
{
  "skeleton": "SELECT _ FROM _ WHERE _ GROUP BY _ HAVING _",
  "expected_clauses": ["SELECT", "FROM", "WHERE", "GROUP BY"]
}
""")


SQL_GENERATION_TMPL = Template("""\
Generate ${n_candidates} diverse SQL queries to answer the question.
Use the skeleton as your structural guide.

## Question
${question}

## SQL Skeleton
${skeleton}

## Filtered Schema (${db_id})
${filtered_schema}

## Instructions
- Each SQL must be valid SQLite syntax.
- Vary your approach (different JOIN types, subquery vs. direct join, etc.)
- Do NOT use columns not in the filtered schema.

## Output Format (JSON only, no extra text)
{
  "candidates": [
    "<SQL 1>",
    "<SQL 2>",
    "<SQL 3>"
  ]
}
""")


# ---------------------------------------------------------------------------
# Step D: Execution & Verification
# ---------------------------------------------------------------------------

FAILURE_ANALYSIS_TMPL = Template("""\
You are a SQL debugging expert. Analyze why the executed SQL failed or returned wrong results.

## Original Question
${question}

## Executed SQL
${executed_sql}

## Runtime Error (if any)
${runtime_error}

## Execution Result
${execution_result}

## Gold SQL (if available)
${gold_sql}

## Instructions
- Identify the root cause of the failure.
- Choose from: intent_misinterpreted, schema_missing_table, schema_missing_column,
  schema_hallucinated_element, sql_wrong_aggregation, sql_missing_clause,
  sql_wrong_join, exec_syntax_error, exec_empty_result, exec_wrong_result, unknown.
- Suggest a concrete fix.

## Output Format (JSON only, no extra text)
{
  "predicted_root_cause": "<label>",
  "explanation": "<why it failed>",
  "suggested_fix": "<how to fix>"
}
""")


SQL_REPAIR_TMPL = Template("""\
Repair the SQL query based on the failure analysis below.

## Original Question
${question}

## Failed SQL
${failed_sql}

## Failure Analysis
${failure_analysis}

## Filtered Schema (${db_id})
${filtered_schema}

## Instructions
- Fix ONLY the identified issue. Do not rewrite the entire query unnecessarily.
- The repaired SQL must be valid SQLite syntax.

## Output Format (JSON only, no extra text)
{
  "repaired_sql": "<corrected SQL>",
  "repair_applied": true
}
""")


# ---------------------------------------------------------------------------
# Helper: Render templates
# ---------------------------------------------------------------------------

def render(template: Template, **kwargs: Any) -> str:
    """Render a Template with the given keyword arguments."""
    return template.safe_substitute(**kwargs)


def schema_to_text(schema: Dict[str, List[Dict[str, str]]]) -> str:
    """Convert schema dict to human-readable text for prompt injection."""
    lines = []
    for table, columns in schema.items():
        col_str = ", ".join(
            f"{c['name']} ({c.get('type', 'TEXT')})" for c in columns
        )
        lines.append(f"  {table}({col_str})")
    return "\n".join(lines)
